# RAHBAR chat UI: static files served by nginx on port 8080. The API is not proxied
# here: the Ingress sends /api to the api Service and / to this one, so the page calls
# /api/ask on its own origin.
#
#   docker build -t rahbar-ui:dev ui
#
# nginx-unprivileged already runs as the non-root user nginx (uid 101). ui/nginx.conf
# writes its pid and temp files under /tmp and logs to stdout/stderr, so the pod can set
# readOnlyRootFilesystem: true with an emptyDir mounted at /tmp.

# Base pinned by tag and digest: nginx 1.30 (the stable line) on Alpine 3.24.
FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03

# Our nginx.conf replaces the whole configuration and includes no conf.d, so the base
# image's default site and its start-up scripts (which edit files under /etc/nginx and
# would fail on a read-only filesystem) are removed.
#
# This is already the newest 1.30 build, but its Alpine packages brotli-libs (CRITICAL
# CVE-2026-111497), curl/libcurl (CVE-2026-92392) and libexpat (CVE-2026-113351,
# -117610, -118370) have fixed versions in Alpine 3.24, so only those are upgraded here.
# zlib (HIGH CVE-2026-85091, heap overflow in gz_vacate, fixed in 1.3.2-r1) was added on
# 2026-11-06, when CI's Trivy scan found it after the CVE was published.
USER root
RUN apk upgrade --no-cache brotli-libs curl libcurl libexpat zlib \
 && rm -f /etc/nginx/conf.d/default.conf /usr/share/nginx/html/*.html \
 && rm -rf /docker-entrypoint.d /docker-entrypoint.sh
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html styles.css app.js favicon.svg /usr/share/nginx/html/
USER 101
EXPOSE 8080
ENTRYPOINT ["nginx", "-g", "daemon off;"]
