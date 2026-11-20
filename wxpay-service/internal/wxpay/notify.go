package wxpay

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// Notification 是微信支付回调通知的外层结构（未解密）。
type Notification struct {
	ID           string `json:"id"`            // 通知唯一 ID，形如 EV-xxx，用于幂等去重
	CreateTime   string `json:"create_time"`   // 通知创建时间
	EventType    string `json:"event_type"`    // 如 MCHTRANSFER.BILL.FINISHED
	Summary      string `json:"summary"`       // 回调摘要
	ResourceType string `json:"resource_type"` // 固定 encrypt-resource
	Resource     struct {
		Algorithm      string `json:"algorithm"`       // 固定 AEAD_AES_256_GCM
		Ciphertext     string `json:"ciphertext"`      // Base64 密文
		AssociatedData string `json:"associated_data"` // 附加数据
		Nonce          string `json:"nonce"`           // 加密随机串
		OriginalType   string `json:"original_type"`   // 原始回调类型，如 mch-transfer
	} `json:"resource"`
}

// VerifyAndParse 完成回调处理的标准流程：验签 -> 解析 -> 解密。
//
// 顺序不可颠倒：先验签，验签通过后才允许解析与解密；
// 未验签即信任内容是资金损失的入口。
//
// rawBody 必须是 HTTP 原始报文体，禁止被框架重序列化。
func VerifyAndParse(keys KeyProvider, apiV3Key string, header http.Header, rawBody []byte) (*Notification, []byte, error) {
	serial := header.Get("Wechatpay-Serial")
	signature := header.Get("Wechatpay-Signature")
	timestamp := header.Get("Wechatpay-Timestamp")
	nonce := header.Get("Wechatpay-Nonce")

	pub, err := keys.PublicKeyBySerial(serial)
	if err != nil {
		return nil, nil, err
	}
	// 注意：官方不要求对 SIGNTEST 探测流量做特殊放行，必须走正常验签流程（必然失败）。
	if err := VerifySignature(pub, timestamp, nonce, string(rawBody), signature); err != nil {
		return nil, nil, err
	}

	var n Notification
	if err := json.Unmarshal(rawBody, &n); err != nil {
		return nil, nil, fmt.Errorf("wxpay: unmarshal notification: %w", err)
	}
	if n.Resource.Ciphertext == "" {
		return nil, nil, fmt.Errorf("wxpay: notification has empty ciphertext")
	}
	plain, err := DecryptResource(apiV3Key, n.Resource.Nonce, n.Resource.AssociatedData, n.Resource.Ciphertext)
	if err != nil {
		return nil, nil, err
	}
	return &n, plain, nil
}
