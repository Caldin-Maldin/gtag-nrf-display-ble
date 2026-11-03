#pragma once
#include <cstddef>
#include <cstdint>
#include "esphome/core/defines.h"
#include "frame_protocol.h"

namespace esphome::gtag_display_ble::firmware_info {
// Firmware version is independent of the HA integration and ESPHome versions.
// Bump for every published firmware; capabilities, not version comparisons,
// decide which packets a host may send.
inline constexpr char VERSION[] = "1.3.0";
constexpr uint8_t SCHEMA = 1;
constexpr size_t SIZE = 40;
static_assert(sizeof(VERSION) <= 20);
inline void write16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
inline void make(uint8_t *out) {
  for (size_t i = 0; i < SIZE; ++i) out[i] = 0;
  out[0] = SCHEMA;
  out[1] = out[2] = frame::PROTOCOL_VERSION;  // Inclusive supported range.
  out[3] = 1;           // Pixel format: row-LSB, 1 = white.
  for (uint8_t codec = 0; codec < 32; ++codec)
    if (frame::codec_supported(static_cast<frame::Codec>(codec)))
      out[4 + codec / 8] |= 1U << (codec % 8);
  out[8] = 1;           // Feature bit 0: freshness lease/confirmation.
#ifdef USE_GTAG_BATTERY
  out[8] |= 2 | 4 | 8;  // ADC, battery bar, low-voltage protection support.
#endif
  write16(out + 12, 256);
  write16(out + 14, 128);
  write16(out + 16, frame::MAX_ENCODED_SIZE);
  // BLE v1 carries one 18-byte payload per GATT write (ATT payload minus the
  // two-byte offset header of the FRAME characteristic).
  write16(out + 18, 18);
  for (size_t i = 0; i < sizeof(VERSION) - 1; ++i) out[20 + i] = VERSION[i];
}
}  // namespace esphome::gtag_display_ble::firmware_info
