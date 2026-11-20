package httpsrv

import (
	"crypto/subtle"
	"log/slog"
	"net/http"
	"runtime/debug"
	"time"
)

// recoverer 捕获 panic，避免单个请求打挂整个进程（资金服务的可用性底线）。
func recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				slog.Error("panic recovered",
					"path", r.URL.Path,
					"panic", rec,
					"stack", string(debug.Stack()),
				)
				writeFail(w, http.StatusInternalServerError, "internal error")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

// requestLog 记录访问日志与耗时。
func requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		wrapped := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(wrapped, r)
		slog.Info("http request",
			"method", r.Method,
			"path", r.URL.Path,
			"status", wrapped.status,
			"cost_ms", time.Since(start).Milliseconds(),
			"remote", r.RemoteAddr,
		)
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// internalAuth 校验内部调用令牌（FastAPI -> Go）。
// 使用常量时间比较，避免时序侧信道。
func internalAuth(token string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if token == "" {
			writeJSON(w, http.StatusInternalServerError, codeFail, "internal token not configured", nil)
			return
		}
		got := r.Header.Get("X-Internal-Token")
		if subtle.ConstantTimeCompare([]byte(got), []byte(token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, codeUnauz, "invalid internal token", nil)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// methodOnly 限制 HTTP 方法（避免依赖 Go 1.22 的路由方法匹配）。
func methodOnly(method string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != method {
			w.Header().Set("Allow", method)
			writeFail(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		next.ServeHTTP(w, r)
	})
}
