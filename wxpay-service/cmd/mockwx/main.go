// Command mockwx 启动本地「微信支付 APIv3」模拟器，让 wxpay-service 在无支付权限的
// 测试服务号下也能跑通完整链路（建单 → 查单 → 回调 → 状态扭转）。
//
// 用法：
//
//	go run ./cmd/mockwx -genkey ./testdata   # 1) 生成模拟器密钥对
//	go run ./cmd/mockwx                      # 2) 启动模拟器（默认 :9200）
//
// 生成密钥后，把公钥配给 wxpay-service（见下方 generateKeys 的输出），
// 并把 WXPAY_API_BASE_URL 指向本模拟器。
package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/mockwx"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

// defaultPublicKeyID 与 wxpay-service 的 WXPAY_PUBLIC_KEY_ID 必须一致。
const defaultPublicKeyID = "PUB_KEY_ID_MOCK000000001"

func main() {
	if err := run(); err != nil {
		slog.Error("mockwx exited with error", "error", err)
		os.Exit(1)
	}
}

func run() error {
	genKeyDir := flag.String("genkey", "", "生成模拟器密钥对到指定目录后退出")
	flag.Parse()
	if *genKeyDir != "" {
		return generateKeys(*genKeyDir)
	}

	// 与 wxpay-service 共用同一个 .env（只读取 MOCK_* 及作为默认值的 WXPAY_*）。
	config.LoadDotenv(".env")

	keyPath := envStr("MOCK_PRIVATE_KEY_PATH", "")
	if keyPath == "" {
		return errors.New("MOCK_PRIVATE_KEY_PATH is required; run with -genkey <dir> first")
	}
	privateKey, err := wxpay.LoadPrivateKey(keyPath)
	if err != nil {
		return err
	}

	// APIv3 密钥默认复用 wxpay-service 的配置：回调密文必须用同一把钥匙加解密。
	apiV3Key := envStr("MOCK_API_V3_KEY", os.Getenv("WXPAY_API_V3_KEY"))
	if len(apiV3Key) != 32 {
		return fmt.Errorf("MOCK_API_V3_KEY/WXPAY_API_V3_KEY must be 32 bytes, got %d", len(apiV3Key))
	}

	// 应答 Serial 必须能被 wxpay-service 查到。与上面两项目保持一致的三段回落：
	// MOCK_PUBLIC_KEY_ID -> WXPAY_PUBLIC_KEY_ID -> defaultPublicKeyID。
	// 少了中间这一段时，只要 .env 里的 WXPAY_PUBLIC_KEY_ID 不是默认常量，
	// 模拟器就会用硬编码值签名，而 wxpay-service 用自己的 ID 查表，
	// 于是每个接口都报：wxpay: no public key for serial "PUB_KEY_ID_MOCK000000001"。
	publicKeyID := envStr("MOCK_PUBLIC_KEY_ID", envStr("WXPAY_PUBLIC_KEY_ID", defaultPublicKeyID))

	srv, err := mockwx.NewServer(mockwx.Options{
		PublicKeyID:   publicKeyID,
		PrivateKey:    privateKey,
		APIv3Key:      apiV3Key,
		MchID:         envStr("MOCK_MCH_ID", envStr("WXPAY_MCH_ID", "MOCK_MCHID")),
		InitialState:  envStr("MOCK_INITIAL_STATE", "WAIT_USER_CONFIRM"),
		AutoSettle:    envBool("MOCK_AUTO_SETTLE"),
		SettleDelay:   time.Duration(envInt("MOCK_SETTLE_DELAY_SEC", 3)) * time.Second,
		SettleState:   envStr("MOCK_SETTLE_STATE", "SUCCESS"),
		NotifyTimeout: time.Duration(envInt("MOCK_NOTIFY_TIMEOUT_SEC", 5)) * time.Second,
	})
	if err != nil {
		return err
	}

	addr := envStr("MOCK_LISTEN_ADDR", ":9200")
	httpSrv := &http.Server{
		Addr:              addr,
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		slog.Info("mockwx starting",
			"addr", addr,
			// 与 wxpay-service 的 WXPAY_PUBLIC_KEY_ID 必须一致，不一致时验签必定失败。
			"public_key_id", publicKeyID,
			"initial_state", envStr("MOCK_INITIAL_STATE", "WAIT_USER_CONFIRM"),
			"auto_settle", envBool("MOCK_AUTO_SETTLE"),
		)
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	select {
	case err := <-errCh:
		return err
	case sig := <-stop:
		slog.Info("mockwx shutdown signal received", "signal", sig.String())
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(ctx); err != nil {
		return err
	}
	slog.Info("mockwx stopped")
	return nil
}

// generateKeys 生成模拟器的 RSA-2048 密钥对，并打印 wxpay-service 侧需要配的三项。
func generateKeys(dir string) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return err
	}

	privPath := filepath.Join(dir, "mock_platform_private_key.pem")
	privPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "RSA PRIVATE KEY",
		Bytes: x509.MarshalPKCS1PrivateKey(key),
	})
	if err := os.WriteFile(privPath, privPEM, 0o600); err != nil {
		return err
	}

	pubDER, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		return err
	}
	pubPath := filepath.Join(dir, "mock_platform_public_key.pem")
	pubPEM := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubDER})
	if err := os.WriteFile(pubPath, pubPEM, 0o644); err != nil {
		return err
	}

	fmt.Printf("已生成模拟器密钥对：\n  私钥（模拟器持有，勿外传）: %s\n  公钥（交给 wxpay-service 验签）: %s\n\n",
		privPath, pubPath)
	fmt.Printf("请在 wxpay-service 的 .env 中配置：\n")
	fmt.Printf("  MOCK_PRIVATE_KEY_PATH=%s\n", privPath)
	fmt.Printf("  WXPAY_PUBLIC_KEY_PATH=%s\n", pubPath)
	fmt.Printf("  WXPAY_PUBLIC_KEY_ID=%s\n", defaultPublicKeyID)
	fmt.Printf("  WXPAY_API_BASE_URL=http://127.0.0.1:9200\n")
	return nil
}

func envStr(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

func envInt(name string, def int) int {
	v := strings.TrimSpace(os.Getenv(name))
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return n
}

func envBool(name string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(name))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}
