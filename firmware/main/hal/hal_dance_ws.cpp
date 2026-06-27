/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 *
 * Dedicated dance side-channel client.
 *
 * Opens a second WebSocket to the bridge server, separate from XiaoZhi voice /ws,
 * and receives choreography frames:
 *
 *   [type(1)][len(4 big-endian)][json payload]
 *
 * type 0x14 is a DanceSequence payload accepted by DanceModifier.
 */
#include "hal.h"

#include <arpa/inet.h>
#include <board.h>
#include <esp_log.h>
#include <esp_mac.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <settings.h>
#include <stackchan/json/json_helper.h>
#include <stackchan/modifiers/dance.h>
#include <stackchan/stackchan.h>
#include <web_socket.h>
#include <wifi_manager.h>

#include <cstring>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <vector>

using namespace stackchan;

static const char* kTag = "DanceWS";

namespace {

std::string device_mac()
{
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char buf[18];
    snprintf(buf, sizeof(buf), "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    return std::string(buf);
}

std::string dance_url_from_settings()
{
    Settings settings("websocket", false);
    std::string url = settings.GetString("url");
    if (url.empty()) {
        return "";
    }

    const std::string suffix = "/ws";
    if (url.size() >= suffix.size() && url.compare(url.size() - suffix.size(), suffix.size(), suffix) == 0) {
        url.erase(url.size() - suffix.size());
    }
    return url + "/dance/ws?deviceId=" + device_mac();
}

class DanceWsClient {
public:
    void run_forever()
    {
        for (;;) {
            if (!_ws || !_ws->IsConnected()) {
                try_connect();
            }
            drain_and_apply();
            if (_dance_until != 0 && GetHAL().millis() > _dance_until) {
                GetStackChan().motion().setModifyLock(false);
                _dance_until = 0;
            }
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }

private:
    void try_connect()
    {
        uint32_t now = GetHAL().millis();
        if (now - _last_attempt < 5000) {
            return;
        }
        _last_attempt = now;

        std::string url = dance_url_from_settings();
        if (url.empty()) {
            ESP_LOGW(kTag, "no websocket url in NVS yet; will retry");
            return;
        }
        if (!WifiManager::GetInstance().IsConnected()) {
            return;
        }

        auto network = Board::GetInstance().GetNetwork();
        if (!network) {
            return;
        }
        _ws = network->CreateWebSocket(1);
        if (!_ws) {
            ESP_LOGE(kTag, "CreateWebSocket failed");
            return;
        }

        _ws->SetReceiveBufferSize(96 * 1024);
        _ws->OnConnected([]() { ESP_LOGI(kTag, "dance channel connected"); });
        _ws->OnDisconnected([]() { ESP_LOGW(kTag, "dance channel disconnected"); });
        _ws->OnData([this](const char* data, size_t len, bool binary) {
            if (!binary || len < 5) {
                return;
            }
            if ((uint8_t)data[0] != 0x14) {
                return;
            }

            uint32_t dlen = 0;
            memcpy(&dlen, data + 1, 4);
            dlen = ntohl(dlen);
            if (5 + (size_t)dlen > len) {
                ESP_LOGW(kTag, "dance frame length mismatch: header=%u have=%u", dlen, (unsigned)(len - 5));
                return;
            }

            std::lock_guard<std::mutex> lock(_mtx);
            _queue.emplace(data + 5, data + 5 + dlen);
        });

        ESP_LOGI(kTag, "connecting dance channel -> %s", url.c_str());
        if (!_ws->Connect(url.c_str())) {
            ESP_LOGE(kTag, "dance channel connect failed");
        }
    }

    void drain_and_apply()
    {
        std::vector<std::vector<uint8_t>> batch;
        {
            std::lock_guard<std::mutex> lock(_mtx);
            while (!_queue.empty()) {
                batch.push_back(std::move(_queue.front()));
                _queue.pop();
            }
        }

        for (auto& payload : batch) {
            payload.push_back('\0');
            apply_dance(reinterpret_cast<const char*>(payload.data()));
        }
    }

    void apply_dance(const char* json)
    {
        auto sequence = animation::parse_sequence_from_json(json);
        if (sequence.empty()) {
            ESP_LOGW(kTag, "empty/invalid dance sequence");
            return;
        }

        uint32_t total_ms = 0;
        for (const auto& kf : sequence) {
            total_ms += kf.durationMs;
        }
        ESP_LOGI(kTag, "playing dance: %d keyframes, %u ms", (int)sequence.size(), total_ms);

        LvglLockGuard lock;
        GetStackChan().motion().setModifyLock(true);
        _dance_until = GetHAL().millis() + total_ms;

        if (_dance_id >= 0) {
            GetStackChan().removeModifier(_dance_id);
        }
        _dance_id = GetStackChan().addModifier(std::make_unique<DanceModifier>(sequence));
    }

    std::unique_ptr<WebSocket> _ws;
    uint32_t _last_attempt = 0;
    uint32_t _dance_until = 0;
    std::mutex _mtx;
    std::queue<std::vector<uint8_t>> _queue;
    int _dance_id = -1;
};

void dance_ws_task(void* /*arg*/)
{
    auto* client = new DanceWsClient();
    client->run_forever();
    vTaskDelete(nullptr);
}

}  // namespace

extern "C" void start_dance_ws()
{
    xTaskCreatePinnedToCore(dance_ws_task, "dance_ws", 8192, nullptr, 3, nullptr, 0);
}
