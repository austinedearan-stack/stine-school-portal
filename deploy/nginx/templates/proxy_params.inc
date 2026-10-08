# Included by portal.conf (not a template: no ".template" suffix, so it is not rendered or loaded on its own).
proxy_pass http://portal_app;
proxy_http_version 1.1;
proxy_set_header Connection "";  # upstream keepalive
proxy_set_header Host $host;
# Overwrite (not append) so Django, with TRUSTED_PROXY_COUNT=1, reads exactly the address nginx saw.
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Request-ID $request_id;
proxy_connect_timeout 5s;
proxy_read_timeout 60s;
proxy_send_timeout 60s;
proxy_buffering on;
proxy_redirect off;
