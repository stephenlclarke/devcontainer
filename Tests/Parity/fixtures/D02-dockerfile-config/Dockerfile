FROM alpine:3.22 AS base

ARG PARITY_BUILD_ARG=missing
RUN printf '%s\n' "$PARITY_BUILD_ARG" > /etc/devcontainer-build-arg

FROM base AS development
RUN printf '%s\n' "development" > /etc/devcontainer-build-target
