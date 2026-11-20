// Command server 是 wxpay-service 的入口。
//
// 职责边界：
//   - 持有商户私钥与 APIv3 密钥，完成 APIv3 签名 / 验签 / 加解密；
//   - 暴露少量内部 HTTP 接口供 FastAPI 调用；
//   - 接收微信支付回调，验签解密后推送给 FastAPI；
//   - 不直连业务数据库，不做业务状态机（由 FastAPI 侧统一持有）。
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/downstream"
	"github.com/OpenRobotService/wxpay-service/internal/httpsrv"
	"github.com/OpenRobotService/wxpay-service/internal/store"
	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

func main() {
	if err := run(); err != nil {
		slog.Error("service exited with error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	setupLogger(cfg.LogLevel)
	if cfg.APIBaseURL != "" {
		// 生产误配会把真实资金请求发到 mock，必须显式告警。
		slog.Warn("WXPAY_API_BASE_URL is overridden: requests will NOT reach WeChat Pay",
			"api_base_url", cfg.APIBaseURL)
	}

	// 1. 加载密钥材料。
	privateKey, err := wxpay.LoadPrivateKey(cfg.PrivateKeyPath)
	if err != nil {
		return err
	}
	wechatPayPublicKey, err := wxpay.LoadPublicKey(cfg.WechatPayPublicKeyPath)
	if err != nil {
		return err
	}
	keys := wxpay.NewStaticKeyProvider(cfg.WechatPayPublicKeyID, wechatPayPublicKey, nil)

	// 2. 构造 APIv3 客户端。
	client, err := wxpay.NewClient(wxpay.ClientConfig{
		BaseURL:         cfg.APIBaseURL,
		MchID:           cfg.MchID,
		CertSerialNo:    cfg.CertSerialNo,
		PrivateKey:      privateKey,
		APIv3Key:        cfg.APIv3Key,
		WechatPaySerial: cfg.WechatPayPublicKeyID,
		Keys:            keys,
		Timeout:         time.Duration(cfg.APITimeoutSec) * time.Second,
	})
	if err != nil {
		return err
	}

	transferSvc := transfer.NewService(client, cfg, wechatPayPublicKey)
	downstreamClient := downstream.NewClient(
		cfg.FastAPIBaseURL,
		cfg.FastAPIToken,
		config.DownstreamNotifyPath,
		time.Duration(cfg.FastAPINotifyTimeout)*time.Second,
	)
	deduper := store.NewDeduper(24*time.Hour, 100000)

	handler := httpsrv.NewRouter(httpsrv.Deps{
		Cfg:         cfg,
		TransferSvc: transferSvc,
		Keys:        keys,
		Deduper:     deduper,
		Downstream:  downstreamClient,
	})

	srv := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           handler,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       time.Duration(cfg.ReadTimeoutSec) * time.Second,
		WriteTimeout:      time.Duration(cfg.WriteTimeoutSec) * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		slog.Info("wxpay-service starting",
			"addr", cfg.ListenAddr,
			"mch_id", cfg.MchID,
			"app_id", cfg.AppID,
			"notify_url", cfg.NotifyURL(),
		)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	select {
	case err := <-errCh:
		return err
	case sig := <-stop:
		slog.Info("shutdown signal received", "signal", sig.String())
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(cfg.ShutdownTimeoutSec)*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		return err
	}
	slog.Info("wxpay-service stopped")
	return nil
}

func setupLogger(level string) {
	var lvl slog.Level
	switch level {
	case "DEBUG":
		lvl = slog.LevelDebug
	case "WARN":
		lvl = slog.LevelWarn
	case "ERROR":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: lvl})))
}
