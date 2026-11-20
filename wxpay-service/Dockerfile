# ---- build ----
FROM golang:1.22-alpine AS builder

WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal

# 零第三方依赖，无需 go mod download；开启 CGO=0 产静态二进制。
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/wxpay-service ./cmd/server

# ---- runtime ----
FROM gcr.io/distroless/static-debian12:nonroot

WORKDIR /app
COPY --from=builder /out/wxpay-service /app/wxpay-service

# 密钥通过 secret / volume 挂载到 /etc/wxpay，不要打进镜像。
VOLUME ["/etc/wxpay"]

EXPOSE 9100
USER nonroot:nonroot

ENTRYPOINT ["/app/wxpay-service"]
