// ======================================
// Name: app_httpd.cpp
// ======================================
#include "esp_http_server.h"
#include "esp_camera.h"

static httpd_handle_t stream_httpd = NULL;

static esp_err_t stream_handler(httpd_req_t *req) {
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

    if (res != ESP_OK) break;
  }
  return res;
}

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();

  httpd_uri_t stream_uri = {
    .uri = "/stream",
    .method = HTTP_GET,
    .handler = stream_handler,
    .user_ctx = NULL
  };

  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}


// #include "esp_http_server.h"
// #include "esp_timer.h"
// #include "esp_camera.h"

// typedef struct {
//   httpd_req_t *req;
//   size_t len;
// } jpg_chunking_t;

// #define PART_BOUNDARY "123456789000000000000987654321"
// static const char *_STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
// static const char *_STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
// static const char *_STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\nX-Timestamp: %d.%06d\r\n\r\n";

// httpd_handle_t stream_httpd = NULL;

// static size_t jpg_encode_stream(void *arg, size_t index, const void *data, size_t len) {
//   httpd_req_t *req = (httpd_req_t *)arg;
//   if (httpd_resp_send_chunk(req, (const char *)data, len) != ESP_OK) {
//     return 0;
//   }
//   return len;
// }

// static esp_err_t stream_handler(httpd_req_t *req) {
//   camera_fb_t *fb = NULL;
//   struct timeval _timestamp;
//   esp_err_t res = ESP_OK;
//   size_t _jpg_buf_len = 0;
//   uint8_t *_jpg_buf = NULL;
//   char part_buf[128];

//   res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
//   if (res != ESP_OK) return res;

//   httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

//   while (true) {
//     fb = esp_camera_fb_get();
//     if (!fb) {
//       res = ESP_FAIL;
//     } else {
//       _timestamp.tv_sec = fb->timestamp.tv_sec;
//       _timestamp.tv_usec = fb->timestamp.tv_usec;
//       if (fb->format != PIXFORMAT_JPEG) {
//         bool jpeg_converted = frame2jpg(fb, 80, &_jpg_buf, &_jpg_buf_len);
//         esp_camera_fb_return(fb);
//         fb = NULL;
//         if (!jpeg_converted) {
//           res = ESP_FAIL;
//         }
//       } else {
//         _jpg_buf_len = fb->len;
//         _jpg_buf = fb->buf;
//       }
//     }

//     if (res == ESP_OK) res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
//     if (res == ESP_OK) {
//       size_t hlen = snprintf((char *)part_buf, 128, _STREAM_PART, _jpg_buf_len, _timestamp.tv_sec, _timestamp.tv_usec);
//       res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
//     }
//     if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);

//     if (fb) esp_camera_fb_return(fb);
//     else if (_jpg_buf) free(_jpg_buf);

//     if (res != ESP_OK) break;
//   }

//   return res;
// }

// void startCameraServer() {
//   httpd_config_t config = HTTPD_DEFAULT_CONFIG();
//   config.max_uri_handlers = 2;

//   httpd_uri_t stream_uri = {
//     .uri = "/stream",
//     .method = HTTP_GET,
//     .handler = stream_handler,
//     .user_ctx = NULL
//   };

//   if (httpd_start(&stream_httpd, &config) == ESP_OK) {
//     httpd_register_uri_handler(stream_httpd, &stream_uri);
//   }
// }