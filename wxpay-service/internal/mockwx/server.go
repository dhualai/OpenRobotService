// Package mockwx 实现「微信支付 APIv3 · 商家转账」的本地模拟器，用于开发与联调。
//
// # 为什么必须有独立的密钥对
//
// 真实的应答/回调是用**微信的平台私钥**签名的，而商户只能从商户平台下载微信支付**公钥**，
// 永远拿不到平台私钥 —— 因此商户无法伪造出一个能通过验签的微信应答。
//
// 模拟器的做法是自签一对 RSA 密钥：
//
//	模拟器持有私钥  -> 给应答和回调签名；
//	模拟器导出公钥  -> 配置给 wxpay-service 的 WXPAY_PUBLIC_KEY_PATH，使其信任模拟器。
//
// 二者是同一对密钥，因此验签链路（时间戳 + 随机串 + 报文体 的三行验签串）、
// APIv3 回调的 AES-256-GCM 加解密、以及 user_name 的 RSA-OAEP 加解密
// 都会被**完整地真实执行**，只是报文来源换成了本机。
//
// # 覆盖范围
//
//   - POST /v3/fund-app/mch-transfer/transfer-bills              发起转账
//   - GET  /v3/fund-app/mch-transfer/transfer-bills/out-bill-no/{no}      按商户单号查单
//   - GET  /v3/fund-app/mch-transfer/transfer-bills/transfer-bill-no/{no} 按微信单号查单
//   - POST /v3/fund-app/mch-transfer/transfer-bills/out-bill-no/{no}/cancel 撤销
//   - /mock/...  管理接口：查看单据、推进状态、重推回调、注入失败
//
// 状态存在内存中，重启即清空 —— 它是联调工具，不是数据库。
package mockwx

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha1"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

const (
	// APIPrefix 是商家转账 API 的公共前缀。
	APIPrefix = "/v3/fund-app/mch-transfer/transfer-bills"

	eventFinished  = "MCHTRANSFER.BILL.FINISHED"
	resourceType   = "encrypt-resource"
	resourceAlgo   = "AEAD_AES_256_GCM"
	originalTypeM  = "mch-transfer"
	maxBodyBytes   = 1 << 20
	defaultTimeout = 5 * time.Second
)

// Options 是模拟器的配置。
type Options struct {
	// PublicKeyID 是应答/回调 Wechatpay-Serial 的值，必须与 wxpay-service 的
	// WXPAY_PUBLIC_KEY_ID 完全一致，否则验签时找不到公钥。
	PublicKeyID string
	// PrivateKey 是模拟器的平台私钥，用于签名；其公钥交给 wxpay-service 验签。
	PrivateKey *rsa.PrivateKey
	// APIv3Key 必须与 wxpay-service 的 WXPAY_API_V3_KEY 完全一致（回调密文用它加解密）。
	APIv3Key string
	// MchID 非空时校验请求中的 mchid 是否匹配。
	MchID string
	// InitialState 是建单后的初始状态，默认 WAIT_USER_CONFIRM（用户确认模式）。
	InitialState string
	// AutoSettle 为 true 时建单后自动推进到 SettleState 并推送回调。
	AutoSettle bool
	// SettleDelay 是自动推进的延迟。
	SettleDelay time.Duration
	// SettleState 是自动推进的目标状态。
	SettleState string
	// NotifyTimeout 是推送回调的超时。
	NotifyTimeout time.Duration
}

// FailSpec 描述下一次「发起转账」要注入的失败。
type FailSpec struct {
	Status  int    `json:"status"`
	Code    string `json:"code"`
	Message string `json:"message"`
}

// bill 是模拟器内部保存的转账单。
type bill struct {
	MchID          string `json:"mch_id"`
	Appid          string `json:"appid"`
	OutBillNo      string `json:"out_bill_no"`
	TransferBillNo string `json:"transfer_bill_no"`
	State          string `json:"state"`
	Amount         int64  `json:"transfer_amount"`
	Remark         string `json:"transfer_remark"`
	FailReason     string `json:"fail_reason"`
	Openid         string `json:"openid"`
	UserName       string `json:"user_name"`
	CreateTime     string `json:"create_time"`
	UpdateTime     string `json:"update_time"`

	// 以下字段仅模拟器内部使用，不参与对 wxpay-service 的应答。
	PackageInfo string `json:"package_info"`
	NotifyURL   string `json:"notify_url"`
}

// Server 是模拟器。
type Server struct {
	opts Options
	cred wxpay.Credential

	mu       sync.Mutex
	bills    map[string]*bill // out_bill_no -> bill
	seq      int64
	failNext *FailSpec

	http *http.Client
}

// NewServer 构造模拟器。
func NewServer(opts Options) (*Server, error) {
	if opts.PrivateKey == nil {
		return nil, fmt.Errorf("mockwx: platform private key is required")
	}
	if len(opts.APIv3Key) != 32 {
		return nil, fmt.Errorf("mockwx: APIv3 key must be 32 bytes, got %d", len(opts.APIv3Key))
	}
	if strings.TrimSpace(opts.InitialState) == "" {
		opts.InitialState = string(transfer.StatusWaitUserConfirm)
	}
	if strings.TrimSpace(opts.SettleState) == "" {
		opts.SettleState = string(transfer.StatusSuccess)
	}
	if opts.SettleDelay <= 0 {
		opts.SettleDelay = 3 * time.Second
	}
	if opts.NotifyTimeout <= 0 {
		opts.NotifyTimeout = defaultTimeout
	}
	return &Server{
		opts: opts,
		cred: wxpay.Credential{SerialNo: opts.PublicKeyID, PrivateKey: opts.PrivateKey},
		bills: make(map[string]*bill),
		http:  &http.Client{Timeout: opts.NotifyTimeout},
	}, nil
}

// Handler 返回模拟器的 HTTP 路由。
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc(APIPrefix+"/", s.handleTransferBills)
	mux.HandleFunc(APIPrefix, s.handleTransferBills)
	mux.HandleFunc("/mock/", s.handleAdmin)
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"service": "mockwx",
			"endpoints": []string{
				"POST " + APIPrefix,
				"GET  " + APIPrefix + "/out-bill-no/{out_bill_no}",
				"GET  " + APIPrefix + "/transfer-bill-no/{transfer_bill_no}",
				"POST " + APIPrefix + "/out-bill-no/{out_bill_no}/cancel",
				"GET  /mock/bills",
				"GET  /mock/bills/{out_bill_no}",
				"POST /mock/bills/{out_bill_no}/settle   {\"state\":\"SUCCESS\",\"fail_reason\":\"\"}",
				"POST /mock/bills/{out_bill_no}/notify",
				"POST /mock/fail-next-create            {\"status\":400,\"code\":\"NAME_ERROR\",\"message\":\"...\"}",
				"POST /mock/reset",
			},
		})
	})
	return mux
}

// ---------- 微信支付 API ----------

func (s *Server) handleTransferBills(w http.ResponseWriter, r *http.Request) {
	rest := strings.Trim(strings.TrimPrefix(r.URL.Path, APIPrefix), "/")

	switch {
	case rest == "" && r.Method == http.MethodPost:
		s.createBill(w, r)

	case strings.HasPrefix(rest, "out-bill-no/") && r.Method == http.MethodGet:
		s.query(w, s.findByOutBillNo(strings.TrimPrefix(rest, "out-bill-no/")))

	case strings.HasPrefix(rest, "out-bill-no/") && r.Method == http.MethodPost &&
		strings.HasSuffix(rest, "/cancel"):
		no := strings.TrimSuffix(strings.TrimPrefix(rest, "out-bill-no/"), "/cancel")
		s.cancelBill(w, no)

	case strings.HasPrefix(rest, "transfer-bill-no/") && r.Method == http.MethodGet:
		s.query(w, s.findByTransferBillNo(strings.TrimPrefix(rest, "transfer-bill-no/")))

	default:
		writeWechatErr(w, http.StatusNotFound, "NOT_FOUND", "unsupported mock api path")
	}
}

func (s *Server) createBill(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes))
	if err != nil {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "read body failed")
		return
	}
	var req transfer.CreateRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "invalid json: "+err.Error())
		return
	}

	// 注入式失败：用于验证 Go/FastAPI 侧的错误分支（如姓名不符、余额不足）。
	if f := s.takeFailNext(); f != nil {
		slog.Info("mockwx: injected create failure", "status", f.Status, "code", f.Code)
		writeWechatErr(w, f.Status, f.Code, f.Message)
		return
	}

	if req.OutBillNo == "" {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "out_bill_no is required")
		return
	}
	if req.Openid == "" {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "openid is required")
		return
	}
	if req.TransferAmount < transfer.MinAmountFen {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "transfer_amount too small")
		return
	}
	if len([]rune(req.TransferRemark)) > transfer.MaxRemarkLen {
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "transfer_remark too long")
		return
	}

	s.mu.Lock()
	if _, exists := s.bills[req.OutBillNo]; exists {
		s.mu.Unlock()
		writeWechatErr(w, http.StatusBadRequest, "PARAM_ERROR", "out_bill_no already exists")
		return
	}
	s.seq++
	now := nowStr()
	b := &bill{
		MchID:          s.opts.MchID,
		Appid:          req.Appid,
		OutBillNo:      req.OutBillNo,
		TransferBillNo: fmt.Sprintf("103%017d", s.seq),
		State:          s.opts.InitialState,
		Amount:         req.TransferAmount,
		Remark:         req.TransferRemark,
		Openid:         req.Openid,
		CreateTime:     now,
		UpdateTime:     now,
		NotifyURL:      req.NotifyURL,
	}

	// user_name 是 RSA-OAEP 加密后传来的；模拟器持有平台私钥，可以解出来验证加密是否正确。
	if req.UserName != "" {
		b.UserName = s.decryptUserName(req.UserName)
		if b.UserName == "" {
			slog.Warn("mockwx: cannot decrypt user_name, encryption may be wrong",
				"out_bill_no", b.OutBillNo)
		}
	}
	if b.State == string(transfer.StatusWaitUserConfirm) {
		b.PackageInfo = fmt.Sprintf("mch_id=%s&app_id=%s&out_bill_no=%s&mock_package=%s",
			b.MchID, b.Appid, b.OutBillNo, wxpay.RandomNonce())
	}
	s.bills[b.OutBillNo] = b

	// 在锁内取好日志与应答所需的快照，避免解锁后读写共享对象。
	resp := transfer.CreateResponse{
		OutBillNo:      b.OutBillNo,
		TransferBillNo: b.TransferBillNo,
		CreateTime:     b.CreateTime,
		State:          transfer.TransferBillStatus(b.State),
		PackageInfo:    b.PackageInfo,
	}
	logArgs := []any{
		"out_bill_no", b.OutBillNo,
		"transfer_bill_no", b.TransferBillNo,
		"state", b.State,
		"amount", b.Amount,
		"user_name", b.UserName,
		"notify_url", b.NotifyURL,
	}
	s.mu.Unlock()

	slog.Info("mockwx: bill created", logArgs...)

	if s.opts.AutoSettle {
		out := resp.OutBillNo
		go func() {
			time.Sleep(s.opts.SettleDelay)
			if _, err := s.settle(out, s.opts.SettleState, ""); err != nil {
				slog.Warn("mockwx: auto settle failed", "out_bill_no", out, "error", err)
			}
		}()
	}

	s.writeSigned(w, http.StatusOK, resp)
}

func (s *Server) query(w http.ResponseWriter, b *bill) {
	if b == nil {
		writeWechatErr(w, http.StatusNotFound, "NOT_FOUND", "bill not found")
		return
	}
	e := transfer.BillEntity{
		MchID:          b.MchID,
		OutBillNo:      b.OutBillNo,
		TransferBillNo: b.TransferBillNo,
		Appid:          b.Appid,
		State:          transfer.TransferBillStatus(b.State),
		TransferAmount: b.Amount,
		TransferRemark: b.Remark,
		FailReason:     b.FailReason,
		Openid:         b.Openid,
		UserName:       b.UserName,
		CreateTime:     b.CreateTime,
		UpdateTime:     b.UpdateTime,
	}
	s.writeSigned(w, http.StatusOK, e)
}

func (s *Server) cancelBill(w http.ResponseWriter, outBillNo string) {
	s.mu.Lock()
	b := s.bills[outBillNo]
	if b == nil {
		s.mu.Unlock()
		writeWechatErr(w, http.StatusNotFound, "NOT_FOUND", "bill not found")
		return
	}
	b.State = string(transfer.StatusCancelled)
	b.PackageInfo = ""
	b.UpdateTime = nowStr()
	resp := transfer.CancelResponse{
		OutBillNo:      b.OutBillNo,
		TransferBillNo: b.TransferBillNo,
		State:          transfer.TransferBillStatus(b.State),
		UpdateTime:     b.UpdateTime,
	}
	s.mu.Unlock()
	slog.Info("mockwx: bill cancelled", "out_bill_no", outBillNo)
	s.writeSigned(w, http.StatusOK, resp)
}

// ---------- 管理接口 ----------

func (s *Server) handleAdmin(w http.ResponseWriter, r *http.Request) {
	rest := strings.Trim(strings.TrimPrefix(r.URL.Path, "/mock/"), "/")

	switch {
	case rest == "bills" && r.Method == http.MethodGet:
		s.mu.Lock()
		out := make([]*bill, 0, len(s.bills))
		for _, b := range s.bills {
			out = append(out, copyBill(b))
		}
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, out)

	case strings.HasPrefix(rest, "bills/") && r.Method == http.MethodGet:
		b := s.findByOutBillNo(strings.TrimPrefix(rest, "bills/"))
		if b == nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"message": "bill not found"})
			return
		}
		writeJSON(w, http.StatusOK, b)

	case strings.HasPrefix(rest, "bills/") && r.Method == http.MethodPost &&
		strings.HasSuffix(rest, "/settle"):
		no := strings.TrimSuffix(strings.TrimPrefix(rest, "bills/"), "/settle")
		var in struct {
			State      string `json:"state"`
			FailReason string `json:"fail_reason"`
		}
		_ = json.NewDecoder(io.LimitReader(r.Body, maxBodyBytes)).Decode(&in)
		if in.State == "" {
			in.State = string(transfer.StatusSuccess)
		}
		b, err := s.settle(no, in.State, in.FailReason)
		if err != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"message": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, b)

	case strings.HasPrefix(rest, "bills/") && r.Method == http.MethodPost &&
		strings.HasSuffix(rest, "/notify"):
		no := strings.TrimSuffix(strings.TrimPrefix(rest, "bills/"), "/notify")
		b := s.findByOutBillNo(no)
		if b == nil {
			writeJSON(w, http.StatusNotFound, map[string]string{"message": "bill not found"})
			return
		}
		// 只推一次：这里的用途是重放同一笔通知，验证 Go 侧去重与 FastAPI 幂等。
		err := s.pushNotify(b)
		writeJSON(w, http.StatusOK, map[string]any{
			"out_bill_no":  b.OutBillNo,
			"state":        b.State,
			"notify":       notifyResult(err),
			"notify_error": errString(err),
		})

	case rest == "fail-next-create" && r.Method == http.MethodPost:
		var f FailSpec
		if err := json.NewDecoder(io.LimitReader(r.Body, maxBodyBytes)).Decode(&f); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": err.Error()})
			return
		}
		if f.Status == 0 {
			f.Status = http.StatusBadRequest
		}
		if f.Code == "" {
			f.Code = "PARAM_ERROR"
		}
		s.mu.Lock()
		s.failNext = &f
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, f)

	case rest == "reset" && r.Method == http.MethodPost:
		s.mu.Lock()
		s.bills = make(map[string]*bill)
		s.failNext = nil
		s.seq = 0
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]string{"status": "reset"})

	default:
		writeJSON(w, http.StatusNotFound, map[string]string{"message": "unknown admin path"})
	}
}

// settle 推进单据状态；到达终态时推送回调。
func (s *Server) settle(outBillNo, state, failReason string) (*bill, error) {
	s.mu.Lock()
	b := s.bills[outBillNo]
	if b == nil {
		s.mu.Unlock()
		return nil, fmt.Errorf("bill not found: %s", outBillNo)
	}
	b.State = state
	b.FailReason = failReason
	b.UpdateTime = nowStr()
	if state == string(transfer.StatusWaitUserConfirm) {
		b.PackageInfo = fmt.Sprintf("mch_id=%s&app_id=%s&out_bill_no=%s&mock_package=%s",
			b.MchID, b.Appid, b.OutBillNo, wxpay.RandomNonce())
	} else {
		b.PackageInfo = ""
	}
	snapshot := copyBill(b)
	s.mu.Unlock()

	slog.Info("mockwx: bill settled", "out_bill_no", outBillNo, "state", state, "fail_reason", failReason)

	// 非终态不推送回调（真实微信也只在终态时通知）。
	if !transfer.IsTerminal(state) {
		return snapshot, nil
	}
	if err := s.pushNotify(snapshot); err != nil {
		slog.Warn("mockwx: push notify failed", "out_bill_no", outBillNo, "error", err)
	}
	return snapshot, nil
}

// ---------- 回调推送 ----------

// pushNotify 构造并推送一笔「转账结果通知」。
//
// 报文结构与真实微信一致：整体 JSON 用平台私钥签名放 Wechatpay-* 头，
// 业务内容用 APIv3 密钥做 AES-256-GCM 加密后放在 resource 里。
//
// 入参必须是快照副本，函数内不再加锁。
func (s *Server) pushNotify(b *bill) error {
	url := b.NotifyURL
	plain, err := json.Marshal(transfer.NotifyResource{
		MchID:          b.MchID,
		OutBillNo:      b.OutBillNo,
		TransferBillNo: b.TransferBillNo,
		Appid:          b.Appid,
		State:          transfer.TransferBillStatus(b.State),
		TransferAmount: b.Amount,
		TransferRemark: b.Remark,
		FailReason:     b.FailReason,
		Openid:         b.Openid,
		UserName:       b.UserName,
		CreateTime:     b.CreateTime,
		UpdateTime:     b.UpdateTime,
	})
	if err != nil {
		return err
	}
	if url == "" {
		return fmt.Errorf("bill %s has no notify_url", b.OutBillNo)
	}

	nonce := wxpay.RandomNonce()[:12]
	ciphertext, err := wxpay.EncryptResource(s.opts.APIv3Key, nonce, originalTypeM, plain)
	if err != nil {
		return err
	}

	n := struct {
		ID           string `json:"id"`
		CreateTime   string `json:"create_time"`
		EventType    string `json:"event_type"`
		Summary      string `json:"summary"`
		ResourceType string `json:"resource_type"`
		Resource     struct {
			Algorithm      string `json:"algorithm"`
			Ciphertext     string `json:"ciphertext"`
			AssociatedData string `json:"associated_data"`
			Nonce          string `json:"nonce"`
			OriginalType   string `json:"original_type"`
		} `json:"resource"`
	}{
		ID:           "EV-" + wxpay.RandomNonce()[:20],
		CreateTime:   nowStr(),
		EventType:    eventFinished,
		Summary:      "商家转账",
		ResourceType: resourceType,
	}
	n.Resource.Algorithm = resourceAlgo
	n.Resource.Ciphertext = ciphertext
	n.Resource.AssociatedData = originalTypeM
	n.Resource.Nonce = nonce
	n.Resource.OriginalType = originalTypeM

	body, err := json.Marshal(n)
	if err != nil {
		return err
	}

	ts := strconv.FormatInt(time.Now().Unix(), 10)
	respNonce := wxpay.RandomNonce()
	sig, err := wxpay.SignResponseMessage(s.cred, ts, respNonce, string(body))
	if err != nil {
		return err
	}

	req, err := http.NewRequest(http.MethodPost, url, strings.NewReader(string(body)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	req.Header.Set("Wechatpay-Serial", s.opts.PublicKeyID)
	req.Header.Set("Wechatpay-Timestamp", ts)
	req.Header.Set("Wechatpay-Nonce", respNonce)
	req.Header.Set("Wechatpay-Signature", sig)

	resp, err := s.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes))
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("notify rejected: status=%d body=%s", resp.StatusCode, string(respBody))
	}
	slog.Info("mockwx: notify delivered",
		"notify_id", n.ID, "out_bill_no", b.OutBillNo, "state", b.State)
	return nil
}

// ---------- 内部工具 ----------

// writeSigned 写入带微信验签头的应答。
func (s *Server) writeSigned(w http.ResponseWriter, status int, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		writeWechatErr(w, http.StatusInternalServerError, "SYSTEM_ERROR", err.Error())
		return
	}
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	nonce := wxpay.RandomNonce()
	sig, err := wxpay.SignResponseMessage(s.cred, ts, nonce, string(body))
	if err != nil {
		writeWechatErr(w, http.StatusInternalServerError, "SYSTEM_ERROR", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Request-ID", wxpay.RandomNonce()[:16])
	w.Header().Set("Wechatpay-Serial", s.opts.PublicKeyID)
	w.Header().Set("Wechatpay-Timestamp", ts)
	w.Header().Set("Wechatpay-Nonce", nonce)
	w.Header().Set("Wechatpay-Signature", sig)
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// findByOutBillNo 返回单据快照（副本）。
// 必须返回副本：调用方会在锁外读字段，直接返回内部指针会造成数据竞争。
func (s *Server) findByOutBillNo(no string) *bill {
	s.mu.Lock()
	defer s.mu.Unlock()
	return copyBill(s.bills[no])
}

func (s *Server) findByTransferBillNo(no string) *bill {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, b := range s.bills {
		if b.TransferBillNo == no {
			return copyBill(b)
		}
	}
	return nil
}

func copyBill(b *bill) *bill {
	if b == nil {
		return nil
	}
	c := *b
	return &c
}

func (s *Server) takeFailNext() *FailSpec {
	s.mu.Lock()
	defer s.mu.Unlock()
	f := s.failNext
	s.failNext = nil
	return f
}

// decryptUserName 用平台私钥解开 RSA-OAEP 加密的收款人姓名。
// 解不出来只记日志、不阻断流程 —— 它只是用来验证 Go 侧加密是否正确的旁路检查。
func (s *Server) decryptUserName(enc string) string {
	raw, err := base64.StdEncoding.DecodeString(enc)
	if err != nil {
		return ""
	}
	plain, err := rsa.DecryptOAEP(sha1.New(), rand.Reader, s.opts.PrivateKey, raw, nil)
	if err != nil {
		return ""
	}
	return string(plain)
}

func notifyResult(err error) string {
	if err == nil {
		return "sent"
	}
	return "failed"
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func nowStr() string { return time.Now().Format(time.RFC3339) }

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeWechatErr(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}
