# gtag_display_ble

ESPHome-компонент для G-Tag 256×128 (1 bpp, 4-wire bit-bang LCD) на nRF52840.
Это BLE-only выделение из компонента `gtag_display`: удалены Wi-Fi-профиль
(ESP32/ESP8266) и Zigbee-транспорт целиком — вместе со всеми ветками кода,
`USE_GTAG_ZIGBEE` / `USE_GTAG_WIFI` / `USE_ESP32` / `USE_ESP8266`, ZBOSS-обвязкой,
`--wrap=zigbee_enable` / `zigbee_default_signal_handler`, телеметрией в HA и
фабрикой endpoint'ов.

## Состав

| файл | роль |
|---|---|
| `__init__.py` | YAML-схема, валидация пинов, Zephyr overlay/prj.conf |
| `gtag_display.h/.cpp` | GATT-сервер, приём кадра, драйвер LCD, кнопка, батарея |
| `frame_protocol.h` | `frame::Receiver`, `Descriptor`, статус, ошибки |
| `frame_codec.h` | `RAW`, `WHITE_RLE_V1`, шаблонные кодеки, CRC32 |
| `freshness.h` | аренда кадра + XOR-иконка «устарело» |
| `template_render.h` | рендер шаблонов 1..4 (шрифты DejaVu, LZSS) |
| `template_font_v1.h`, `template_font_large_v1.h`, `template_clock_v1.h` | сгенерированные ассеты |
| `boot_logo.h`, `battery_messages.h` | статические кадры |
| `battery_bar.h`, `battery_guard.h` | OCV-индикатор и защита по напряжению |
| `firmware_info.h` | INFO-характеристика (capabilities) |
| `flash_storage.h` | чтение flash/PROGMEM-ассетов |

## GATT

Сервис `7a1e0011-6b5b-4f6d-8d6e-0f4f47544147` (UUID сохранены как в оригинале):

| UUID | характеристика | доступ |
|---|---|---|
| `...0012` | LED (виртуальный тестовый) | read/write |
| `...0013` | FRAME (`offset:u16` + данные) | write / write-no-response |
| `...0014` | STATUS (8 байт) | read |
| `...0015` | CONTROL (BEGIN/COMMIT/FRESHNESS) | write |
| `...0016` | BATTERY (mV, LE) | read |
| `...0017` | INFO (40 байт) | read |
| `...0018` | BUTTON | read/notify |

## Пример конфигурации

```yaml
external_components:
  - source:
      type: local
      path: components
    components: [gtag_display_ble]

esp32:  # surrogate: build board для nRF52
  board: adafruit_itsybitsy_nrf52840

nrf52:
  bootloader: adafruit_nrf52_bootloader

gtag_display_ble:
  id: tag_lcd
  dio_pin: P0.11
  clk_pin: P1.04
  cs_pin: P1.06
  reset_pin: P1.13
  tx_power: 8
  advertising_interval: 1s
  boot_test_pattern: logo
  button_pin: P0.17
  button_sensor: tag_button
  battery_voltage:
    enabled: true
    pin: P0.31
    calibration: 1.0
    empty_voltage: 3.306V
    full_voltage: 4.19V
    recovery_voltage: 3.45V
```

`battery_voltage.enabled: false` полностью убирает ADC, индикатор и защиту;
`button_pin` опционален, `button_sensor` требует `button_pin`.

## Отличия от исходного `gtag_display` (BLE-ветка)

- Убраны `transport:` — профиль всегда `ble`; наличие компонента `zigbee` в
  конфигурации теперь ошибка валидации.
- Убраны `zigbee_*` ID, `rendered_frames_sensor_id`, `battery_voltage.sensor_id`.
- Исправлена инициализация SAADC: `NRF_SAADC_AIN0` вместо опечатки и
  корректный признак «вход найден» (раньше P0.02 молча отключал измерение).
- `battery_guard.h` больше не содержит `RadioStartGate` — он был нужен только
  для отложенного старта ZBOSS.
- C++ namespace: `esphome::gtag_display_ble`.
