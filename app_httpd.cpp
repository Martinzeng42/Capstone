// ======================================
// Name: app_httpd.cpp
// ======================================
#include "esp_http_server.h"
#include "esp_camera.h"
// ======================================
// HTTP Server handles
// ======================================
httpd_handle_t main_httpd = NULL;
bool stream_active = false;
// ======================================
// Stream handler (/stream)
// ======================================
static esp_err_t stream_handler(httpd_req_t *req) {
  if (!stream_active) {
    return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "Stream not active");
  }

  camera_fb_t *fb = NULL;
  esp_err_t res = ESP_OK;

  res = httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=frame");
  if (res != ESP_OK) return res;

  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      continue;
    }

    res = httpd_resp_send_chunk(req, "--frame\r\n", strlen("--frame\r\n"));
    res |= httpd_resp_send_chunk(req, "Content-Type: image/jpeg\r\n\r\n", strlen("Content-Type: image/jpeg\r\n\r\n"));
    res |= httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
    res |= httpd_resp_send_chunk(req, "\r\n", strlen("\r\n"));

    esp_camera_fb_return(fb);

    if (res != ESP_OK || !stream_active) break;
  }

  return res;
}

// ======================================
// Handler for /start_preview
// ======================================
static esp_err_t preview_start_handler(httpd_req_t *req) {
  stream_active = true;
  httpd_resp_sendstr(req, "Preview started");
  return ESP_OK;
}

// ======================================
// Handler for /stop_preview
// ======================================
static esp_err_t preview_stop_handler(httpd_req_t *req) {
  stream_active = false;
  httpd_resp_sendstr(req, "Preview stopped");
  return ESP_OK;
}

// ======================================
// Start command server for preview control
// ======================================
void startWebServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();

  httpd_uri_t start_uri = {
    .uri = "/start_preview",
    .method = HTTP_GET,
    .handler = preview_start_handler,
    .user_ctx = NULL
  };

  httpd_uri_t stop_uri = {
    .uri = "/stop_preview",
    .method = HTTP_GET,
    .handler = preview_stop_handler,
    .user_ctx = NULL
  };

  httpd_uri_t stream_uri = {
    .uri = "/stream",
    .method = HTTP_GET,
    .handler = stream_handler,
    .user_ctx = NULL
  };

  if (httpd_start(&main_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(main_httpd, &start_uri);
    httpd_register_uri_handler(main_httpd, &stop_uri);
    httpd_register_uri_handler(main_httpd, &stream_uri);
    printf("HTTP server started on port 80");
  } else {
    printf("❌ Failed to start HTTP server");
  }
}

