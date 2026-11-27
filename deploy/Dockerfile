# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim
ARG NGINX_IMAGE=nginx:stable-alpine

FROM ${PYTHON_IMAGE} AS dependencies
WORKDIR /build
COPY requirements-web.txt ./
RUN python -m pip install --no-cache-dir --prefix=/install -r requirements-web.txt

FROM ${PYTHON_IMAGE} AS ui-build
WORKDIR /build
COPY scripts/build_ui_site.py ./scripts/build_ui_site.py
COPY design/hs-rule-ui ./design/hs-rule-ui
RUN python scripts/build_ui_site.py && rm .build/hs-rule-ui/_headers .build/hs-rule-ui/_redirects

FROM ${PYTHON_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY --from=dependencies /install /usr/local
COPY server.py index.html login.html upload.html HSU_logo.png ./
COPY routers ./routers
COPY hsrule_web ./hsrule_web
COPY hsrule/__init__.py hsrule/migrations.py ./hsrule/
COPY deploy/bootstrap.py ./deploy/bootstrap.py
RUN install -d -m 0700 -o 65532 -g 65532 /data/uploads
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=172.30.82.10", "--no-access-log"]

FROM ${NGINX_IMAGE} AS web
COPY deploy/nginx.conf /etc/nginx/nginx.conf
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/*
COPY --from=ui-build /build/.build/hs-rule-ui/ /usr/share/nginx/html/
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
