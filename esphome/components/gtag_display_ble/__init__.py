"""G-Tag LCD: nRF52840 BLE profile.

BLE-only subset of the original ``gtag_display`` component. The ESP32/ESP8266
Wi-Fi profile and the Zigbee frame transport have been removed, together with
every code path and Kconfig option that only existed to support them.
"""
import re

import voluptuous as vol

from esphome import pins
import esphome.codegen as cg
import esphome.config_validation as cv
import esphome.final_validate as fv

from esphome.const import CONF_ID
from esphome.core import CORE
from esphome.components.zephyr import zephyr_add_overlay, zephyr_add_prj_conf
from esphome.components.nrf52.const import AIN_TO_GPIO
from esphome.components.nrf52.gpio import validate_gpio_pin
from esphome.components import binary_sensor

DEPENDENCIES = []
# ``gtag_display`` is the multi-profile original; both would define the same
# nRF52 overlays and claim the same LCD pins.
CONFLICTS_WITH = [
    "bthome",
    "gtag_display",
    "gtag_ble_test",
    "lcd_standalone",
    "lcd_gpio_guard",
]
MULTI_CONF = False

CONF_TX_POWER = "tx_power"
CONF_ADVERTISING_INTERVAL = "advertising_interval"
CONF_BOOT_TEST_PATTERN = "boot_test_pattern"
CONF_BATTERY_VOLTAGE = "battery_voltage"
CONF_CALIBRATION = "calibration"
CONF_BUTTON_PIN = "button_pin"
CONF_BUTTON_SENSOR = "button_sensor"
LCD_PINS = ("dio_pin", "clk_pin", "cs_pin", "reset_pin")

ns = cg.esphome_ns.namespace("gtag_display_ble")
GTagDisplay = ns.class_("GTagDisplay", cg.Component)
BootPattern = ns.enum("BootPattern", is_class=True)
BOOT_PATTERNS = {
    "logo": BootPattern.LOGO,
    "none": BootPattern.NONE,
    "white": BootPattern.WHITE,
    "black": BootPattern.BLACK,
    "checkerboard": BootPattern.CHECKERBOARD,
    "stripes": BootPattern.STRIPES,
}


def advertising_interval(value):
    interval = cv.positive_time_period_milliseconds(value)
    if not 100 <= interval.total_milliseconds <= 10240:
        raise cv.Invalid("advertising_interval must be between 100ms and 10240ms")
    return interval


def gpio_number(value):
    """Use physical nRF GPIO numbers, not board-dependent D/A aliases."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise cv.Invalid("Use a GPIO number such as P0.11; mode and inversion are fixed by the LCD protocol")
    if isinstance(value, str) and value.startswith("P"):
        match = re.fullmatch(r"P([01])\.(\d{1,2})", value)
        if not match or int(match[2]) >= (32 if match[1] == "0" else 16):
            raise cv.Invalid("Expected P0.00..P0.31 or P1.00..P1.15")
    number = validate_gpio_pin(value)
    if not isinstance(number, int) or not 0 <= number <= 47:
        raise cv.Invalid("Expected an external GPIO on nRF52840 (0..47)")
    if number in (0, 1, 9, 10, 18):
        raise cv.Invalid("P0.00/P0.01 (32 kHz crystal), P0.09/P0.10 (NFC), and P0.18 (RESET) are reserved")
    return number


def lcd_pin(value):
    return pins.internal_gpio_output_pin_number(gpio_number(value))


def battery_pin(value):
    number = gpio_number(value)
    if number not in AIN_TO_GPIO.values():
        raise cv.Invalid("Battery ADC requires P0.02..P0.05 or P0.28..P0.31")
    return pins.internal_gpio_input_pin_number(number)


def button_pin(value):
    """Active-low button on nRF52, internal pull-up configured in C++."""
    return gpio_number(value)


def validate_pin_assignment(config):
    used = {}
    assignments = [(key, config[key], [key]) for key in LCD_PINS]
    if config[CONF_BATTERY_VOLTAGE]["enabled"]:
        assignments.append(
            ("battery_voltage.pin", config[CONF_BATTERY_VOLTAGE]["pin"], [CONF_BATTERY_VOLTAGE, "pin"])
        )
    if CONF_BUTTON_PIN in config:
        assignments.append((CONF_BUTTON_PIN, config[CONF_BUTTON_PIN], [CONF_BUTTON_PIN]))
    for name, number, path in assignments:
        if isinstance(number, dict):
            number = number["number"]
        if number in used:
            raise cv.Invalid(f"GPIO {number} is already used by {used[number]}", path)
        used[number] = name
    return config


def validate_battery_range(config):
    if not config["enabled"]:
        return config
    if round(config["empty_voltage"] * 1000) >= round(config["full_voltage"] * 1000):
        raise cv.Invalid("full_voltage must exceed empty_voltage by at least 1 mV", ["full_voltage"])
    if not round(config["empty_voltage"] * 1000) < round(config["recovery_voltage"] * 1000) <= round(
        config["full_voltage"] * 1000
    ):
        raise cv.Invalid("recovery_voltage must exceed empty_voltage and not exceed full_voltage", ["recovery_voltage"])
    return config


def battery_config(value):
    value = cv.Schema({cv.Required("enabled"): cv.boolean}, extra=vol.ALLOW_EXTRA)(value)
    unknown = set(value) - {
        "enabled",
        "pin",
        CONF_CALIBRATION,
        "indicator",
        "empty_voltage",
        "full_voltage",
        "recovery_voltage",
    }
    if unknown:
        raise cv.Invalid(f"Unknown battery option: {sorted(unknown)[0]}")
    if not value["enabled"]:
        # Package merging may leave enabled-only fields behind; validate them
        # only when measurement is enabled, and never reserve their GPIO.
        return {"enabled": False}
    return validate_battery_range(
        cv.Schema(
            {
                cv.Required("enabled"): cv.boolean,
                cv.Required("pin"): battery_pin,
                cv.Required(CONF_CALIBRATION): cv.float_range(min=0.8, max=1.2),
                cv.Optional("indicator", default=True): cv.boolean,
                cv.Required("empty_voltage"): cv.All(cv.voltage, cv.Range(min=2.5, max=4.5)),
                cv.Required("full_voltage"): cv.All(cv.voltage, cv.Range(min=2.5, max=4.5)),
                cv.Required("recovery_voltage"): cv.All(cv.voltage, cv.Range(min=2.5, max=4.5)),
            }
        )(value)
    )


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(GTagDisplay),
            cv.Optional(CONF_TX_POWER, default=0): cv.one_of(0, 4, 8, int=True),
            cv.Optional(CONF_ADVERTISING_INTERVAL, default="1s"): advertising_interval,
            cv.Optional(CONF_BOOT_TEST_PATTERN, default="logo"): cv.enum(BOOT_PATTERNS, lower=True),
            cv.Optional(CONF_BUTTON_PIN): button_pin,
            cv.Optional(CONF_BUTTON_SENSOR): cv.use_id(binary_sensor.BinarySensor),
            **{cv.Required(key): lcd_pin for key in LCD_PINS},
            # B+ -- 1M -- ADC GPIO -- 1M -- GND; 100nF from ADC GPIO to GND.
            cv.Required(CONF_BATTERY_VOLTAGE): battery_config,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    validate_pin_assignment,
)


def validate_profile(config):
    if not CORE.is_nrf52:
        raise cv.Invalid("gtag_display_ble requires nRF52 (Zephyr)")
    full = fv.full_config.get()
    if "spi" in full or "i2c" in full:
        raise cv.Invalid("The nRF52 battery profile reserves SPI and I2C")
    if "zigbee" in full:
        raise cv.Invalid("gtag_display_ble is BLE-only; remove the zigbee component")
    if CONF_BUTTON_SENSOR in config and CONF_BUTTON_PIN not in config:
        raise cv.Invalid("button_sensor requires button_pin")
    return config


FINAL_VALIDATE_SCHEMA = validate_profile


async def to_code(config):
    if not CORE.is_nrf52:
        raise cv.Invalid("gtag_display_ble requires nRF52")

    # adafruit_itsybitsy_nrf52840 is only the build surrogate for this board.
    zephyr_add_overlay(
        """
        &i2c0 { status = "disabled"; };
        &spi1 { status = "disabled"; };
        &spi2 { status = "disabled"; };
        &apa102 { status = "disabled"; };
        &qspi { status = "disabled"; };
        &gd25q16 { status = "disabled"; };
        &uart0 { status = "disabled"; };
    """
    )

    zephyr_add_prj_conf("SPI", False)
    zephyr_add_prj_conf("I2C", False)
    zephyr_add_prj_conf("PWM", False)
    # The surrogate board enables CDC in its devicetree even without logger.
    # Remove that device too, so the battery build has no USB/UART activity.
    if "logger" not in CORE.config:
        zephyr_add_overlay(
            """
            &usbd { status = "disabled"; };
            &cdc_acm_uart0 { status = "disabled"; };
            / {
                chosen {
                    /delete-property/ zephyr,console;
                    /delete-property/ zephyr,shell-uart;
                    /delete-property/ zephyr,uart-mcumgr;
                    /delete-property/ zephyr,bt-mon-uart;
                    /delete-property/ zephyr,bt-c2h-uart;
                };
            };
        """
        )
        zephyr_add_prj_conf("USB_DEVICE_STACK", False)
        zephyr_add_prj_conf("USB_CDC_ACM", False)
        zephyr_add_prj_conf("SERIAL", False)

    # nRF52's normal idle path supports System ON sleep without CONFIG_PM.
    # Keep the kernel tickless; do not power off the BLE controller or LCD GPIO.
    zephyr_add_prj_conf("TICKLESS_KERNEL", True)

    zephyr_add_prj_conf("BT", True)
    zephyr_add_prj_conf("BT_PERIPHERAL", True)
    zephyr_add_prj_conf("BT_MAX_CONN", 1)
    zephyr_add_prj_conf("BT_DEVICE_NAME", "GTag Display")
    tx_option = {
        0: "BT_CTLR_TX_PWR_0",
        4: "BT_CTLR_TX_PWR_PLUS_4",
        8: "BT_CTLR_TX_PWR_PLUS_8",
    }
    zephyr_add_prj_conf(tx_option[config[CONF_TX_POWER]], True)

    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    cg.add(var.set_advertising_interval(config[CONF_ADVERTISING_INTERVAL].total_milliseconds))
    cg.add(var.set_boot_pattern(config[CONF_BOOT_TEST_PATTERN]))
    for key in LCD_PINS:
        cg.add(getattr(var, f"set_{key}")(config[key]))
    if CONF_BUTTON_PIN in config:
        cg.add_define("USE_GTAG_BUTTON")
        cg.add(var.set_button_pin(config[CONF_BUTTON_PIN]))
        if CONF_BUTTON_SENSOR in config:
            cg.add(var.set_button_sensor(await cg.get_variable(config[CONF_BUTTON_SENSOR])))
    if config[CONF_BATTERY_VOLTAGE]["enabled"]:
        cg.add_define("USE_GTAG_BATTERY")
        zephyr_add_overlay('&adc { status = "okay"; };')
        zephyr_add_prj_conf("ADC", True)
        cg.add(var.set_battery_pin(config[CONF_BATTERY_VOLTAGE]["pin"]))
        cg.add(var.set_battery_calibration(config[CONF_BATTERY_VOLTAGE][CONF_CALIBRATION]))
        cg.add(var.set_battery_indicator(config[CONF_BATTERY_VOLTAGE]["indicator"]))
        cg.add(
            var.set_battery_voltage_range(
                round(config[CONF_BATTERY_VOLTAGE]["empty_voltage"] * 1000),
                round(config[CONF_BATTERY_VOLTAGE]["full_voltage"] * 1000),
            )
        )
        cg.add(
            var.set_battery_protection(
                round(config[CONF_BATTERY_VOLTAGE]["empty_voltage"] * 1000),
                round(config[CONF_BATTERY_VOLTAGE]["recovery_voltage"] * 1000),
            )
        )
