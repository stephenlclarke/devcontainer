FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce AS development

ARG PARITY_BUILD_ARG=missing
RUN printf '%s\n' "$PARITY_BUILD_ARG" > /etc/devcontainer-build-arg

RUN printf '%s\n' "development" > /etc/devcontainer-build-target
