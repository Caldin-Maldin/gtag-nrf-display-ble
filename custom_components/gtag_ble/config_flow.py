from __future__ import annotations

from typing import Any
import logging
import secrets
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_UUID
from .firmware_flow import FirmwareWizardMixin
from .diagnostic_flow import DiagnosticFlowMixin
from .layout_flow import LayoutTransferMixin, excluded_entities
from .layouts import (
    DECIMAL_PLACES, DEFAULT_INTERVAL, DEFAULT_PRESET, PRESETS, VALUE_SOURCES, StaleAfterTooShort,
    async_render_layout, is_text_value, normalize_saved_timing, preset_layout,
    validate_settings, validate_timing, value_count,
)
from .render import clock_layout, preview_svg, get_available_fonts, DEFAULT_FONT

_LOGGER = logging.getLogger(__name__)


class GTagBLETestConfigFlow(FirmwareWizardMixin, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GTagOptionsFlow()

    def __init__(self) -> None:
        self._info: BluetoothServiceInfoBleak | None = None
        self._devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._info is not None

        await self.async_set_unique_id(self._info.address)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title=self._info.name or self._info.address,
                data={CONF_ADDRESS: self._info.address},
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["ble", "firmware"])

    async def async_step_ble(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not await async_setup_component(self.hass, "bluetooth", {}):
            return self.async_abort(reason="bluetooth_not_ready")
        configured = self._async_current_ids(include_ignore=False)

        for info in async_discovered_service_info(self.hass):
            uuids = {uuid.lower() for uuid in info.service_uuids}
            if (
                SERVICE_UUID not in uuids
                or info.address in configured
                or info.address in self._devices
            ):
                continue
            self._devices[info.address] = info

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            info = self._devices[address]

            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=info.name or info.address,
                data={CONF_ADDRESS: address},
            )

        if not self._devices:
            return self.async_abort(reason="no_devices_found")

        choices = {
            address: f"{info.name or 'GTag Display'} ({address})"
            for address, info in self._devices.items()
        }

        return self.async_show_form(
            step_id="ble",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(choices)}
            ),
        )


class GTagOptionsFlow(DiagnosticFlowMixin, LayoutTransferMixin, OptionsFlow):
    """Choose a preset, inspect local pixels, then explicitly apply it."""

    def __init__(self) -> None:
        self._settings: dict[str, Any] | None = None
        self._preview = ""

    def _font_selector(self):
        fonts = get_available_fonts(self.hass)
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=fonts,
                translation_key="font",
                mode=selector.SelectSelectorMode.DROPDOWN
            )
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["configure", "export_layout", "import_layout", "diagnostics"]
        )

    async def async_step_configure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        
        if user_input is not None:
            # Сохраняем выбор пользователя
            settings = {**user_input}
            if settings.get("preset") == "clock":
                settings.pop("layout", None)
                settings.pop("auto_update", None)
            
            # На этом шаге проверяем только тайминги, не требуя entity_1
            try:
                validate_timing(settings)
            except StaleAfterTooShort as err:
                errors["stale_after"] = "stale_after_too_short"
                placeholders["minimum"] = str(err.minimum)
            else:
                self._settings = settings
                preset = settings.get("preset")
                if preset == "three_values":
                    return await self.async_step_value_sources()
                if preset in ("single_value", "clock_two_values", "weather", "value_graph"):
                    return await self.async_step_values()
                return await self.async_step_preview()

        current = self._settings or normalize_saved_timing(
            dict(self.config_entry.options.get("screen", {}))
        )
        
        schema = vol.Schema({
            # Убрали "custom" из списка, так как UI-форма не поддерживает создание кастомных макетов
            vol.Required("preset", default=current.get("preset", DEFAULT_PRESET)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(PRESETS),
                    translation_key="preset",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required("update_interval", default=current.get("update_interval", DEFAULT_INTERVAL)): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=3600)
            ),
            vol.Required("stale_after", default=current.get("stale_after", 15)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=1440)
            ),
            vol.Optional("font_clock", default=current.get("font_clock", DEFAULT_FONT)): self._font_selector(),
        })
        
        return self.async_show_form(
            step_id="configure",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
            description_placeholders=placeholders,
            last_step=False,
        )

    async def async_step_value_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if self._settings is None:
                self._settings = {}
            self._settings.update(user_input)
            return await self.async_step_three_values()
            
        current = self._settings or {}
        schema = vol.Schema({
            vol.Required(f"source_{index}", default=current.get(f"source_{index}", "entity")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(VALUE_SOURCES),
                    translation_key="value_source",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            for index in (3, 1, 2)
        })
        return self.async_show_form(
            step_id="value_sources",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            last_step=False,
        )

    async def async_step_three_values(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._settings is None:
                self._settings = {}
            self._settings.update(user_input)
            try:
                validate_settings(self._settings)
            except vol.Invalid:
                errors["base"] = "invalid_settings"
            else:
                return await self.async_step_preview()
                
        current = self._settings or {}
        excluded = excluded_entities(self.hass)
        font_selector = self._font_selector()
        fields: dict[Any, Any] = {}
        
        for index in (3, 1, 2):
            source = current.get(f"source_{index}", "entity")
            if source == "text":
                fields[vol.Required(f"text_{index}", default=current.get(f"text_{index}", ""))] = selector.TextSelector()
            else:
                fields[vol.Required(f"entity_{index}", default=current.get(f"entity_{index}", vol.UNDEFINED))] = selector.EntitySelector(
                    selector.EntitySelectorConfig(exclude_entities=excluded)
                )
                fields[vol.Optional(f"label_{index}", default=current.get(f"label_{index}", ""))] = selector.TextSelector()
                fields[vol.Optional(f"unit_{index}", default=current.get(f"unit_{index}", ""))] = selector.TextSelector()
                fields[vol.Optional(f"decimals_{index}", default=current.get(f"decimals_{index}", "original"))] = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(DECIMAL_PLACES),
                        translation_key="decimal_places",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            fields[vol.Optional(f"font_{index}", default=current.get(f"font_{index}", DEFAULT_FONT))] = font_selector
            
        return self.async_show_form(
            step_id="three_values",
            data_schema=vol.Schema(fields),
            errors=errors,
            last_step=False,
        )

    async def async_step_values(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._settings is None:
                self._settings = {}
            self._settings.update(user_input)
            try:
                validate_settings(self._settings)
            except vol.Invalid:
                errors["base"] = "invalid_settings"
            else:
                return await self.async_step_preview()
                
        current = self._settings or {}
        preset = current.get("preset")
        excluded = excluded_entities(self.hass)
        domains = ["weather"] if preset == "weather" else ["sensor", "input_number", "number"] if preset == "value_graph" else None
        count = value_count(preset)
        font_selector = self._font_selector()
        fields: dict[Any, Any] = {}
        
        for index in range(1, count + 1):
            fields[vol.Required(f"entity_{index}", default=current.get(f"entity_{index}", vol.UNDEFINED))] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    exclude_entities=excluded,
                    **({"filter": {"domain": domains}} if domains else {}),
                )
            )
            fields[vol.Optional(f"label_{index}", default=current.get(f"label_{index}", ""))] = selector.TextSelector()
            fields[vol.Optional(f"unit_{index}", default=current.get(f"unit_{index}", ""))] = selector.TextSelector()
            fields[vol.Optional(f"decimals_{index}", default=current.get(f"decimals_{index}", "original"))] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(DECIMAL_PLACES),
                    translation_key="decimal_places",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            fields[vol.Optional(f"font_{index}", default=current.get(f"font_{index}", DEFAULT_FONT))] = font_selector
            
        if preset == "value_graph":
            fields[vol.Required("history_hours", default=current.get("history_hours", 24))] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=168)
            )
            
        return self.async_show_form(
            step_id="values",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), current),
            errors=errors,
            last_step=False,
        )

    async def async_step_preview(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        display = getattr(self.config_entry, "runtime_data", None)
        
        try:
            if self._settings.get("preset") == "clock":
                layout = clock_layout(
                    dt_util.now(),
                    font_time=self._settings.get("font_clock", DEFAULT_FONT),
                    font_date=self._settings.get("font_clock", DEFAULT_FONT),
                )
            else:
                layout = preset_layout(self._settings)
            rendered = await async_render_layout(self.hass, layout)
            self._preview = preview_svg(rendered.raw)
        except Exception as err:
            _LOGGER.warning("Preview render failed: %s", err)
            errors["base"] = "render_failed"
            self._preview = ""

        if user_input is not None:
            action = user_input.get("action", "apply")
            if action == "edit":
                return await self.async_step_configure()
            if action == "refresh":
                return await self.async_step_preview()
            if action == "apply":
                if display is None:
                    errors["base"] = "invalid_settings"
                else:
                    try:
                        revision = secrets.token_hex(8)
                        await display.async_apply_settings(self._settings, revision)
                    except Exception as err:
                        _LOGGER.error("Applying screen settings failed: %s", err)
                        errors["base"] = "render_failed"
                    else:
                        return self.async_create_entry(
                            title="",
                            data={**self.config_entry.options,
                                  "screen": self._settings,
                                  "screen_revision": revision},
                        )
                        
        schema = vol.Schema({
            vol.Required("action", default="apply"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["apply", "edit", "refresh"],
                    translation_key="preview_action",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(
            step_id="preview",
            data_schema=schema,
            errors=errors,
            description_placeholders={"preview": self._preview},
            last_step=True,
        )