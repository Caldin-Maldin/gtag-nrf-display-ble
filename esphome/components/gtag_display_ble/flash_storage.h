#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

// nRF52 (Zephyr) has a single flat address space: the generated font, logo and
// message assets live in flash and are read directly. GTAG_PROGMEM stays empty
// so the generated headers keep their upstream form unchanged.
#define GTAG_PROGMEM

namespace esphome::gtag_display_ble::flash_storage {
inline uint8_t byte(const uint8_t *p) { return *p; }
inline uint16_t u16(const uint8_t *p) { return uint16_t(byte(p)) | (uint16_t(byte(p + 1)) << 8); }
inline uint32_t u32(const uint8_t *p) { return u16(p) | (uint32_t(u16(p + 2)) << 16); }
inline void copy(void *out, const void *in, size_t size) { std::memcpy(out, in, size); }
}  // namespace esphome::gtag_display_ble::flash_storage
