// ======================================
// Name: CameraWebServer.ino
// ======================================
#include "esp_camera.h"
#include <WiFi.h>
#include "board_config.h"

// ======================================
// Enter your WiFi credentials
// ======================================
const char *ssid = "Cimols_2.4G";
const char *password = "brocktonPHLIMA";

// ======================================
// Forward declarations
// ======================================
WiFiServer tcpServer(12345);  // TCP server on port 12345
void startWebServer();


void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_VGA;
  config.pixel_format = PIXFORMAT_JPEG;  // for streaming
  //config.pixel_format = PIXFORMAT_RGB565; // for face detection/recognition
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 20;
  config.fb_count = 1;


  if (esp_camera_init(&config) != ESP_OK) {
    Serial.println("Camera init failed");
    return;
  }

  WiFi.begin(ssid, password);
  WiFi.setSleep(false);

  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("");
  Serial.println("WiFi connected");
  Serial.print("ESP32-CAM IP Address: ");
  Serial.print("http://");
  Serial.print(WiFi.localIP());
  Serial.print("/stream");

  // Start only the trigger command server
  tcpServer.begin();
  Serial.println("TCP server started on port 12345");
}

void loop() {
  WiFiClient client = tcpServer.available();
  if (client) {
    Serial.println("Client connected");
    while (client.connected()) {
      camera_fb_t *fb = esp_camera_fb_get();
      if (!fb) {
        Serial.println("Capture failed");
        continue;
      }

      // Send frame length (4 bytes)
      uint32_t frameLen = fb->len;
      client.write((uint8_t*)&frameLen, sizeof(frameLen));

      // Send JPEG frame
      client.write(fb->buf, fb->len);
      esp_camera_fb_return(fb);

      delay(30);  // ~30 FPS cap
    }
    client.stop();
    Serial.println("Client disconnected");
  }
}
