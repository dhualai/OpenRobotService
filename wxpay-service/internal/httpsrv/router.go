// Package httpsrv 提供 wxpay-service 的 HTTP 层：
//   - /internal/**  内部接口，供 FastAPI 调用，需 X-Internal-Token
//   - /wxpay/notify/** 微信支付回调，公网可达，不做登录态校验
package httpsrv

import (
	"net/http"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/downstream"
	"github.com/OpenRobotService/wxpay-service/internal/store"
	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

// Deps 是路由所需的依赖。
type Deps struct {
	Cfg         *config.Config
	TransferSvc *transfer.Service
	Keys        wxpay.KeyProvider
	Deduper     *store.Deduper
	Downstream  *downstream.Client
}

// NewRouter 构造完整路由。
func NewRouter(d Deps) http.Handler {
	mux := http.NewServeMux()

	th := &transferHandler{svc: d.TransferSvc}
	nh := &notifyHandler{
		cfg:        d.Cfg,
		keys:       d.Keys,
		deduper:    d.Deduper,
		downstream: d.Downstream,
	}

	// 健康检查：不加鉴权，供容器探针使用。
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeOK(w, map[string]string{"status": "ok", "service": "wxpay-service"})
	})

	// 内部接口（需鉴权）。
	mux.Handle("/internal/transfer/create",
		requestLog(recoverer(internalAuth(d.Cfg.InternalToken, methodOnly(http.MethodPost, http.HandlerFunc(th.create))))))
	mux.Handle("/internal/transfer/cancel",
		requestLog(recoverer(internalAuth(d.Cfg.InternalToken, methodOnly(http.MethodPost, http.HandlerFunc(th.cancel))))))
	mux.Handle("/internal/transfer/query/",
		requestLog(recoverer(internalAuth(d.Cfg.InternalToken, methodOnly(http.MethodGet, http.HandlerFunc(th.queryByOutBillNo))))))
	mux.Handle("/internal/transfer/query-by-bill/",
		requestLog(recoverer(internalAuth(d.Cfg.InternalToken, methodOnly(http.MethodGet, http.HandlerFunc(th.queryByTransferBillNo))))))

	// 微信支付回调：公网可达，不能加内部鉴权中间件。
	mux.Handle(config.NotifyPathTransfer,
		requestLog(recoverer(methodOnly(http.MethodPost, http.HandlerFunc(nh.transfer)))))

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		writeFail(w, http.StatusNotFound, "not found")
	})

	return mux
}
