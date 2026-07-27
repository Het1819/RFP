# Minimal, repository-owned Nginx image: the official unprivileged Nginx
# base, pinned by tag + immutable digest, with only our own config and
# static error pages added. No extra packages installed.
#
# nginxinc/nginx-unprivileged:1.29-alpine, resolved 2026-07-24 via
# `docker pull nginxinc/nginx-unprivileged:1.29-alpine`. Already runs its
# worker process as a non-root user (uid 101) and listens on unprivileged
# ports (8080/8443 here) by design -- that's the whole point of this base
# image variant. Chosen over 1.27-alpine after a Trivy scan showed it
# carries far fewer OS-package CVEs (12 HIGH / 0 CRITICAL vs. 33 HIGH /
# 2 CRITICAL on 1.27-alpine at the same scan time) -- see the PR
# description for the residual findings still present here.
FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:0c79d56aee561a1d81c63f00eee5fb5fe29279560cdc55e91425133104c7fbe6

COPY nginx.conf /etc/nginx/nginx.conf
COPY proxy_common.conf /etc/nginx/proxy_common.conf
COPY templates/default.conf.template /etc/nginx/templates/default.conf.template
COPY html/errors /usr/share/nginx/html/errors

EXPOSE 8080 8443
