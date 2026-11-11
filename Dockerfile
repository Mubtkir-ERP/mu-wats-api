FROM ghcr.io/shridarpatil/frappe

LABEL org.opencontainers.image.source=https://github.com/Mubtkir-ERP/mu-wats-api
MAINTAINER Mubtkir <info@mubtkir.com>
RUN bench get-app https://github.com/Mubtkir-ERP/mu-wats-api.git --skip-assets
