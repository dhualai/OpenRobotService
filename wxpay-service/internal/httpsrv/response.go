package httpsrv

import (
	"encoding/json"
	"net/http"
)

// envelope 是内部接口（FastAPI -> Go）的统一响应结构。
type envelope struct {
	Code    string      `json:"code"`
	Message string      `json:"message,omitempty"`
	Data    interface{} `json:"data,omitempty"`
}

const (
	codeOK    = "OK"
	codeFail  = "FAIL"
	codeUnauz = "UNAUTHORIZED"
)

func writeJSON(w http.ResponseWriter, status int, code, message string, data interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(envelope{Code: code, Message: message, Data: data})
}

func writeOK(w http.ResponseWriter, data interface{}) {
	writeJSON(w, http.StatusOK, codeOK, "", data)
}

func writeFail(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, codeFail, message, nil)
}

// wechatFail 是给微信支付回调的错误应答：非 2xx，微信会按策略重试。
func wechatFail(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, codeFail, message, nil)
}

// wechatOK 是给微信支付回调的成功应答：200 + 空包体。
func wechatOK(w http.ResponseWriter) {
	w.WriteHeader(http.StatusOK)
}
