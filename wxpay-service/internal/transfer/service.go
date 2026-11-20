package transfer

import (
	"context"
	"crypto/rsa"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"

	"github.com/OpenRobotService/wxpay-service/internal/config"
	"github.com/OpenRobotService/wxpay-service/internal/wxpay"
)

const (
	pathCreate      = "/v3/fund-app/mch-transfer/transfer-bills"
	pathQueryByOut  = "/v3/fund-app/mch-transfer/transfer-bills/out-bill-no/"
	pathQueryByBill = "/v3/fund-app/mch-transfer/transfer-bills/transfer-bill-no/"
	pathCancelSuf   = "/cancel"

	// MinAmountFen 是商家转账单笔最小金额：0.1 元（现金营销场景固定，不可调）。
	MinAmountFen = 10
	// UserNameRequiredAmountFen 是必须传收款人姓名的金额阈值：2000 元。
	UserNameRequiredAmountFen = 200000
	// MaxRemarkLen 是 transfer_remark 的最大长度。
	MaxRemarkLen = 32
	// MaxOutBillNoLen 是商户单号最大长度。
	MaxOutBillNoLen = 32
)

// Service 封装商家转账的 APIv3 调用。
type Service struct {
	client *wxpay.Client
	cfg    *config.Config
	pubKey *rsa.PublicKey // 微信支付公钥，用于 user_name 加密
}

// NewService 构造转账服务。
func NewService(client *wxpay.Client, cfg *config.Config, wechatPayPublicKey *rsa.PublicKey) *Service {
	return &Service{client: client, cfg: cfg, pubKey: wechatPayPublicKey}
}

// CreateInput 是发起转账的入参。
type CreateInput struct {
	OutBillNo  string // 商户单号，只能数字+大小写字母，≤32 字符；每次新建必须是新值
	Openid     string // 收款人在 appid 下的 openid
	UserName   string // 收款人姓名明文；非空则由本服务用微信支付公钥加密后传出
	Amount     int64  // 转账金额，单位：分
	Remark     string // 转账备注，用户可见，≤32 字符
	SceneName  string // 报备信息①活动名称
	RewardDesc string // 报备信息②奖励说明
	Perception string // 收款感知，空则用配置默认值
	SceneID    string // 转账场景 ID，空则用配置默认值
	NotifyURL  string // 回调地址，空则用配置默认值
}

// CreateResult 是发起转账的结果。
type CreateResult struct {
	OutBillNo      string `json:"out_bill_no"`
	TransferBillNo string `json:"transfer_bill_no"`
	State          string `json:"state"`
	PackageInfo    string `json:"package_info"`
	CreateTime     string `json:"create_time"`
}

// Create 发起转账。
//
// ⚠️ 调用方（FastAPI）必须先完成「三分支决策」：
//   - 已有 WAIT_USER_CONFIRM 的转账单 → 复用 package_info，禁止调用本方法；
//   - 已有其它非终态转账单 → 先查单，禁止调用本方法；
//   - 只有不存在转账单或已到终态时，才可用【新的 out_bill_no】调用本方法。
func (s *Service) Create(ctx context.Context, in CreateInput) (*CreateResult, error) {
	if err := s.validate(in); err != nil {
		return nil, err
	}

	req := CreateRequest{
		Appid:              s.cfg.AppID,
		OutBillNo:          in.OutBillNo,
		TransferSceneID:    defaultString(in.SceneID, s.cfg.TransferSceneID),
		Openid:             in.Openid,
		TransferAmount:     in.Amount,
		TransferRemark:     in.Remark,
		NotifyURL:          defaultString(in.NotifyURL, s.cfg.NotifyURL()),
		UserRecvPerception: defaultString(in.Perception, s.cfg.DefaultPerception),
		SceneReportInfos: []SceneReportInfo{
			{InfoType: InfoTypeActivityName, InfoContent: in.SceneName},
			{InfoType: InfoTypeRewardDesc, InfoContent: in.RewardDesc},
		},
	}

	// user_name：非空即加密（≥2000 元时为强制项）。
	if in.UserName != "" {
		encrypted, err := wxpay.EncryptOAEP(s.pubKey, in.UserName)
		if err != nil {
			return nil, err
		}
		req.UserName = encrypted
	}

	body, err := s.client.Do(ctx, "POST", pathCreate, req)
	if err != nil {
		return nil, err
	}
	var resp CreateResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("transfer: unmarshal create response: %w", err)
	}
	return &CreateResult{
		OutBillNo:      resp.OutBillNo,
		TransferBillNo: resp.TransferBillNo,
		State:          string(resp.State),
		PackageInfo:    resp.PackageInfo,
		CreateTime:     resp.CreateTime,
	}, nil
}

// QueryByOutBillNo 按商户单号查询转账单（提现决策 + 兜底查单均依赖此接口）。
func (s *Service) QueryByOutBillNo(ctx context.Context, outBillNo string) (*BillEntity, error) {
	if !isAlnum(outBillNo) {
		return nil, fmt.Errorf("transfer: invalid out_bill_no %q", outBillNo)
	}
	path := pathQueryByOut + url.PathEscape(outBillNo)
	body, err := s.client.Do(ctx, "GET", path, nil)
	if err != nil {
		return nil, err
	}
	var e BillEntity
	if err := json.Unmarshal(body, &e); err != nil {
		return nil, fmt.Errorf("transfer: unmarshal bill entity: %w", err)
	}
	return &e, nil
}

// QueryByTransferBillNo 按微信转账单号查询（兜底）。
func (s *Service) QueryByTransferBillNo(ctx context.Context, transferBillNo string) (*BillEntity, error) {
	if strings.TrimSpace(transferBillNo) == "" {
		return nil, fmt.Errorf("transfer: empty transfer_bill_no")
	}
	path := pathQueryByBill + url.PathEscape(transferBillNo)
	body, err := s.client.Do(ctx, "GET", path, nil)
	if err != nil {
		return nil, err
	}
	var e BillEntity
	if err := json.Unmarshal(body, &e); err != nil {
		return nil, fmt.Errorf("transfer: unmarshal bill entity: %w", err)
	}
	return &e, nil
}

// Cancel 撤销转账。
//
// 仅可在用户确认收款前撤销；返回成功仅代表受理，需再查单确认最终状态。
func (s *Service) Cancel(ctx context.Context, outBillNo string) (*CancelResponse, error) {
	if !isAlnum(outBillNo) {
		return nil, fmt.Errorf("transfer: invalid out_bill_no %q", outBillNo)
	}
	path := pathQueryByOut + url.PathEscape(outBillNo) + pathCancelSuf
	body, err := s.client.Do(ctx, "POST", path, nil)
	if err != nil {
		return nil, err
	}
	var resp CancelResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("transfer: unmarshal cancel response: %w", err)
	}
	return &resp, nil
}

func (s *Service) validate(in CreateInput) error {
	if !isAlnum(in.OutBillNo) || len(in.OutBillNo) > MaxOutBillNoLen {
		return fmt.Errorf("transfer: out_bill_no must be alphanumeric and <= %d chars", MaxOutBillNoLen)
	}
	if strings.TrimSpace(in.Openid) == "" {
		return fmt.Errorf("transfer: openid is required")
	}
	if in.Amount < MinAmountFen {
		return fmt.Errorf("transfer: amount must be >= %d fen (0.1 yuan)", MinAmountFen)
	}
	if s.cfg.MaxTransferAmountFen > 0 && in.Amount > s.cfg.MaxTransferAmountFen {
		return fmt.Errorf("transfer: amount %d fen exceeds configured limit %d fen", in.Amount, s.cfg.MaxTransferAmountFen)
	}
	if in.Amount >= UserNameRequiredAmountFen && strings.TrimSpace(in.UserName) == "" {
		return fmt.Errorf("transfer: user_name is required when amount >= 2000 yuan")
	}
	if in.UserName != "" && s.pubKey == nil {
		return fmt.Errorf("transfer: wechat pay public key is not configured, cannot encrypt user_name")
	}
	if len([]rune(in.Remark)) > MaxRemarkLen {
		return fmt.Errorf("transfer: transfer_remark must be <= %d chars", MaxRemarkLen)
	}
	if strings.TrimSpace(in.SceneName) == "" || strings.TrimSpace(in.RewardDesc) == "" {
		return fmt.Errorf("transfer: scene report infos (活动名称 + 奖励说明) are both required")
	}
	return nil
}

// isAlnum 校验 out_bill_no：只能由数字和大小写字母组成。
func isAlnum(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !((r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z')) {
			return false
		}
	}
	return true
}

func defaultString(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}
