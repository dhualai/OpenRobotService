// Package transfer 封装「商家转账」产品的 APIv3 接口。
package transfer

import "strings"

// TransferBillStatus 是微信侧转账单状态。
type TransferBillStatus string

const (
	StatusAccepted        TransferBillStatus = "ACCEPTED"
	StatusProcessing      TransferBillStatus = "PROCESSING"
	StatusWaitUserConfirm TransferBillStatus = "WAIT_USER_CONFIRM"
	StatusTransfering     TransferBillStatus = "TRANSFERING"
	StatusSuccess         TransferBillStatus = "SUCCESS"
	StatusFail            TransferBillStatus = "FAIL"
	StatusCanceling       TransferBillStatus = "CANCELING"
	StatusCancelled       TransferBillStatus = "CANCELLED"
)

// IsTerminal 判断是否为终态。终态只有 SUCCESS / FAIL / CANCELLED 三个。
func (s TransferBillStatus) IsTerminal() bool {
	switch s {
	case StatusSuccess, StatusFail, StatusCancelled:
		return true
	default:
		return false
	}
}

// CanReusePackageInfo 判断 package_info 是否仍可用于调起用户确认收款页。
//
// 仅 WAIT_USER_CONFIRM 状态下可复用；用户取消确认页后再次点击应走此分支，
// 而不是创建新的转账单（否则重复出资）。
func (s TransferBillStatus) CanReusePackageInfo() bool {
	return s == StatusWaitUserConfirm
}

// IsTerminal 供字符串状态直接调用（回调/查单结果解析后使用）。
func IsTerminal(state string) bool {
	return TransferBillStatus(strings.ToUpper(strings.TrimSpace(state))).IsTerminal()
}

// CanReusePackageInfo 供字符串状态直接调用。
func CanReusePackageInfo(state string) bool {
	return TransferBillStatus(strings.ToUpper(strings.TrimSpace(state))).CanReusePackageInfo()
}

// SceneReportInfo 是转账场景报备信息。
//
// 现金营销场景（1000）的报备信息是固定的两条：「活动名称」+「奖励说明」，缺一即报 PARAM_ERROR。
type SceneReportInfo struct {
	InfoType    string `json:"info_type"`
	InfoContent string `json:"info_content"`
}

const (
	InfoTypeActivityName = "活动名称"
	InfoTypeRewardDesc   = "奖励说明"
)

// CreateRequest 是「发起转账」的请求体。
//
// 路径：POST /v3/fund-app/mch-transfer/transfer-bills
type CreateRequest struct {
	Appid              string            `json:"appid"`
	OutBillNo          string            `json:"out_bill_no"`
	TransferSceneID    string            `json:"transfer_scene_id"`
	Openid             string            `json:"openid"`
	UserName           string            `json:"user_name,omitempty"` // RSA-OAEP 加密后的姓名
	TransferAmount     int64             `json:"transfer_amount"`     // 单位：分
	TransferRemark     string            `json:"transfer_remark"`
	NotifyURL          string            `json:"notify_url,omitempty"`
	UserRecvPerception string            `json:"user_recv_perception,omitempty"`
	SceneReportInfos   []SceneReportInfo `json:"transfer_scene_report_infos"`
}

// CreateResponse 是「发起转账」的应答体。
type CreateResponse struct {
	OutBillNo      string             `json:"out_bill_no"`
	TransferBillNo string             `json:"transfer_bill_no"`
	CreateTime     string             `json:"create_time"`
	State          TransferBillStatus `json:"state"`
	PackageInfo    string             `json:"package_info"` // 仅 WAIT_USER_CONFIRM 时返回
}

// BillEntity 是「查询转账单」的应答体。
type BillEntity struct {
	MchID          string             `json:"mch_id"`
	OutBillNo      string             `json:"out_bill_no"`
	TransferBillNo string             `json:"transfer_bill_no"`
	Appid          string             `json:"appid"`
	State          TransferBillStatus `json:"state"`
	TransferAmount int64              `json:"transfer_amount"`
	TransferRemark string             `json:"transfer_remark"`
	FailReason     string             `json:"fail_reason"`
	Openid         string             `json:"openid"`
	UserName       string             `json:"user_name"`
	CreateTime     string             `json:"create_time"`
	UpdateTime     string             `json:"update_time"`
}

// CancelResponse 是「撤销转账」的应答体。
type CancelResponse struct {
	OutBillNo      string             `json:"out_bill_no"`
	TransferBillNo string             `json:"transfer_bill_no"`
	State          TransferBillStatus `json:"state"`
	UpdateTime     string             `json:"update_time"`
}

// NotifyResource 是商家转账回调解密后的内容（event_type = MCHTRANSFER.BILL.FINISHED）。
type NotifyResource struct {
	MchID          string             `json:"mch_id"`
	OutBillNo      string             `json:"out_bill_no"`
	TransferBillNo string             `json:"transfer_bill_no"`
	Appid          string             `json:"appid"`
	State          TransferBillStatus `json:"state"`
	TransferAmount int64              `json:"transfer_amount"`
	TransferRemark string             `json:"transfer_remark"`
	FailReason     string             `json:"fail_reason"`
	Openid         string             `json:"openid"`
	UserName       string             `json:"user_name"`
	CreateTime     string             `json:"create_time"`
	UpdateTime     string             `json:"update_time"`
}
