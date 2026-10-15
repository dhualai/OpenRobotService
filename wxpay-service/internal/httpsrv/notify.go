package httpsrv

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/downstream"
	"github.com/OpenRobotService/wxpay-service/internal/store"
	"github.com/OpenRobotService/wxpay-service/internal/transfer"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

// EventTypeTransferFinished 是商家转账的回调事件类型。
const EventTypeTransferFinished = "MCHTRANSFER.BILL.FINISHED"

// notifyHandler 处理微信支付回调（对外，无内部鉴权）。
type notifyHandler struct {
	cfg        *config.Config
	keys       wxpay.KeyProvider
	deduper    *store.Deduper
	downstream *downstream.Client
}

// maxNotifyBody 是回调报文体的上限。
// 正常的商家转账回调远小于此；超限直接拒绝，而不是静默截断成验签失败。
const maxNotifyBody = 1 << 20 // 1 MiB

func (h *notifyHandler) transfer(w http.ResponseWriter, r *http.Request) {
	// 1. 读取原始报文体（禁止任何重序列化）。
	if r.ContentLength > maxNotifyBody {
		wechatFail(w, http.StatusRequestEntityTooLarge, "request body too large")
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, maxNotifyBody))
	if err != nil {
		wechatFail(w, http.StatusBadRequest, "read body failed")
		return
	}
	// 读取到上限说明报文被截断（可能是攻击或异常流量），明确拒绝而非交给验签。
	if len(raw) >= maxNotifyBody {
		var probe [1]byte
		if n, _ := r.Body.Read(probe[:]); n > 0 {
			wechatFail(w, http.StatusRequestEntityTooLarge, "request body too large")
			return
		}
	}

	// 2. 验签 -> 解析 -> 解密（顺序不可变）。
	notification, plain, err := wxpay.VerifyAndParse(h.keys, h.cfg.APIv3Key, r.Header, raw)
	if err != nil {
		// 验签失败必须拒绝，且不得更新任何业务状态。
		slog.Warn("notification verification failed", "error", err, "path", r.URL.Path)
		wechatFail(w, http.StatusUnauthorized, "signature verification failed")
		return
	}

	if notification.EventType != EventTypeTransferFinished {
		slog.Warn("unexpected event type", "event_type", notification.EventType)
		wechatFail(w, http.StatusBadRequest, "unexpected event type")
		return
	}

	// 3. 解密后的业务内容。
	var res transfer.NotifyResource
	if err := json.Unmarshal(plain, &res); err != nil {
		slog.Error("unmarshal decrypted resource failed", "error", err)
		wechatFail(w, http.StatusBadRequest, "invalid resource")
		return
	}

	// 4. 幂等判断（第一道防线；最终幂等由 FastAPI 唯一索引保证）。
	//
	// 这里只判断不记录：必须先推送成功才能 Mark。否则推送失败应答 5xx 后，
	// 微信的重试会被误判为「已处理过的重复通知」直接返回 200，
	// 导致该笔回调永久丢失（只能等 FastAPI 兜底查单补偿）。
	if h.deduper.Has(notification.ID) {
		slog.Info("duplicate notification skipped", "notify_id", notification.ID)
		wechatOK(w)
		return
	}

	// 5. 推送给 FastAPI；失败则应答 5xx 让微信重试。
	err = h.downstream.PushTransferNotify(r.Context(), downstream.TransferNotifyPayload{
		NotifyID:         notification.ID,
		EventType:        notification.EventType,
		NotifyCreateTime: notification.CreateTime,
		OriginalType:     notification.Resource.OriginalType,
		MchID:            res.MchID,
		AppID:            res.Appid,
		OutBillNo:        res.OutBillNo,
		TransferBillNo:   res.TransferBillNo,
		Openid:           res.Openid,
		State:            string(res.State),
		TransferAmount:   res.TransferAmount,
		TransferRemark:   res.TransferRemark,
		FailReason:       res.FailReason,
		CreateTime:       res.CreateTime,
		UpdateTime:       res.UpdateTime,
	})
	if err != nil {
		slog.Error("push notify to fastapi failed",
			"notify_id", notification.ID,
			"out_bill_no", res.OutBillNo,
			"state", res.State,
			"error", err,
		)
		wechatFail(w, http.StatusInternalServerError, "downstream unavailable")
		return
	}

	// 6. 推送成功后才标记为已处理，允许微信在失败场景下重试。
	//    重复推送是安全的：FastAPI 侧 wxpay_notify_log.notify_id 唯一索引会幂等拦截。
	h.deduper.Mark(notification.ID)

	slog.Info("transfer notification processed",
		"notify_id", notification.ID,
		"out_bill_no", res.OutBillNo,
		"transfer_bill_no", res.TransferBillNo,
		"state", res.State,
		"amount", res.TransferAmount,
	)
	wechatOK(w)
}
