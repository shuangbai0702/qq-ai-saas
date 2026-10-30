# Build the allowlisted context from npm run docker:prepare, never the live tree.
# Both public base images are immutable. AstrBot application/dependencies below
# are replaced with our recorded 4.29.3 source and frozen uv.lock.
ARG RUNTIME_IMAGE=soulter/astrbot@sha256:0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1
ARG NAPCAT_IMAGE=mlikiowa/napcat-docker@sha256:132448a544ed5cfa948dff8ae19e80bf4e17896e8ab2ba1c6cbcd86f8ad3e56e
FROM ${RUNTIME_IMAGE} AS web-build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN NODE_OPTIONS=--max-old-space-size=512 npm run build:production && npm prune --omit=dev

FROM ${RUNTIME_IMAGE} AS python-build
ARG PYPI_INDEX_URL=https://pypi.org/simple
WORKDIR /opt/astrbot
COPY upstream/astrbot/ ./
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_CONCURRENT_DOWNLOADS=2 UV_CONCURRENT_INSTALLS=1
RUN uv export --frozen --no-dev --no-emit-project --output-file requirements.lock \
 && uv venv .venv \
 && uv pip sync --python .venv/bin/python --require-hashes --index-url "${PYPI_INDEX_URL}" requirements.lock \
 && uv pip check --python .venv/bin/python

FROM ${NAPCAT_IMAGE} AS qq-runtime
FROM ${RUNTIME_IMAGE} AS runtime
ARG DEBIAN_MIRROR=https://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=https://deb.debian.org/debian-security
USER root
RUN sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g;s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=1 update \
 && apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=1 upgrade -y --no-install-recommends \
 && apt-get -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=1 install -y --no-install-recommends tini libx11-6 libxext6 libxrender1 libxrandr2 libxi6 libxau6 libxdmcp6 libxcb1 libbsd0 libmd0 libx11-xcb1 libglib2.0-0 libgssapi-krb5-2 libnss3 libasound2t64 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 fishbot && useradd --uid 10001 --gid 10001 --home-dir /var/lib/fishbot/home --no-create-home fishbot \
 && mkdir -p /app /var/lib/fishbot/home && chown -R 10001:10001 /var/lib/fishbot
# Copy pristine program files from the image, never a container's mounted QQ data.
COPY --from=qq-runtime /opt/QQ/resources/app/wrapper.node /opt/QQ/resources/app/libssh2.so.1 /opt/QQ/resources/app/libcrbase.so /opt/QQ/resources/app/libbugly.so /opt/QQ/resources/app/libunwind.so.8 /opt/QQ/resources/app/libunwind-x86_64.so.8 /opt/QQ/resources/app/package.json /opt/napcat/
COPY --from=qq-runtime /opt/QQ/resources/app/sharp-lib/ /opt/napcat/sharp-lib/
COPY scripts/setup-linux-napcat.py docker/napcat-upstream.json /tmp/napcat-build/
ARG NAPCAT_BUNDLE_URL=
RUN NAPCAT_BUNDLE_URL="${NAPCAT_BUNDLE_URL}" python /tmp/napcat-build/setup-linux-napcat.py /tmp/napcat-build/napcat-upstream.json /opt/napcat/napcat && rm -rf /tmp/napcat-build
COPY upstream/napcat-loader/node_addon.node /opt/napcat/node_addon.node
RUN ln -s /usr/bin/node /opt/napcat/node
COPY --from=python-build /opt/astrbot/ /opt/astrbot/
WORKDIR /app
COPY --from=web-build /build/node_modules/ ./node_modules/
COPY --from=web-build /build/dist-server/ ./dist-server/
COPY --from=web-build /build/apps/web/dist/ ./apps/web/dist/
COPY --from=web-build /build/resources/pglite-template/ ./resources/pglite-template/
COPY adapters/ ./adapters/
COPY packages/contracts/ ./packages/contracts/
COPY templates/ ./templates/
COPY scripts/ ./scripts/
COPY package.json ./package.json
COPY upstream/astrbot-version.json /opt/astrbot/fishbot-upstream.json
COPY docker/napcat-upstream.json /opt/napcat/fishbot-upstream.json
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8787 DATA_ROOT=/var/lib/fishbot HOME=/var/lib/fishbot/home \
 ASTRBOT_SOURCE=/opt/astrbot ASTRBOT_PYTHON=/opt/astrbot/.venv/bin/python NAPCAT_RUNTIME_DIR=/opt/napcat \
 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FISHBOT_ASTRBOT_SLIM=1 DB_MEMORY_PROFILE=compact SHARED_GROUP_CAPACITY=8 DB_LOCK_WAIT_MS=30000
USER 10001:10001
EXPOSE 8787
VOLUME /var/lib/fishbot
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD ["node","scripts/docker-health.mjs"]
ENTRYPOINT ["/usr/bin/tini","--","node","--max-old-space-size=128","scripts/docker-entrypoint.mjs"]
