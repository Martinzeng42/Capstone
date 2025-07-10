#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>

#define CAMERA_MODEL_AI_THINKER

#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

const char* ssid = "ESP32_CAM_AP";
const char* password = "12345678";

WebServer server(80);

void streamHandler() {
  WiFiClient client = server.client();
  String response = "HTTP/1.1 200 OK\r\n";
  response += "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n";
  server.sendContent(response);

  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) continue;
    response = "--frame\r\nContent-Type: image/jpeg\r\n\r\n";
    server.sendContent(response);
    client.write(fb->buf, fb->len);
    server.sendContent("\r\n");
    esp_camera_fb_return(fb);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.softAP(ssid, password);
  delay(1000);
  Serial.println("AP IP address: ");
  Serial.println(WiFi.softAPIP());

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
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  // config.frame_size = FRAMESIZE_QVGA;
  // config.jpeg_quality = 12;
  config.frame_size = FRAMESIZE_CIF;
  config.jpeg_quality = 20;

  config.fb_count = 1;

  esp_camera_init(&config);

  server.on("/", streamHandler);
  server.begin();
}

void loop() {
  server.handleClient();
}
