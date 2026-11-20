package wxpay

import (
	"crypto/rsa"
	"fmt"
)

// KeyProvider 按 Wechatpay-Serial 返回验签用的公钥。
//
// 微信支付公钥模式下 Serial 形如 PUB_KEY_ID_xxxxxxxxxxxxx；
// 平台证书模式下为证书序列号。
type KeyProvider interface {
	PublicKeyBySerial(serial string) (*rsa.PublicKey, error)
}

// StaticKeyProvider 是基于配置的静态实现：微信支付公钥 + 可选平台证书集合。
type StaticKeyProvider struct {
	WechatPayPublicKeyID string
	WechatPayPublicKey   *rsa.PublicKey
	PlatformCerts        map[string]*rsa.PublicKey // 证书序列号 -> 公钥
}

// NewStaticKeyProvider 构造静态 KeyProvider；platformCerts 可为 nil。
func NewStaticKeyProvider(publicKeyID string, publicKey *rsa.PublicKey, platformCerts map[string]*rsa.PublicKey) *StaticKeyProvider {
	if platformCerts == nil {
		platformCerts = map[string]*rsa.PublicKey{}
	}
	return &StaticKeyProvider{
		WechatPayPublicKeyID: publicKeyID,
		WechatPayPublicKey:   publicKey,
		PlatformCerts:        platformCerts,
	}
}

// PublicKeyBySerial 按序列号选择公钥。
//
// 官方规则：以 PUB_KEY_ID_ 开头 -> 微信支付公钥；否则 -> 对应平台证书。
func (p *StaticKeyProvider) PublicKeyBySerial(serial string) (*rsa.PublicKey, error) {
	if serial == p.WechatPayPublicKeyID && p.WechatPayPublicKey != nil {
		return p.WechatPayPublicKey, nil
	}
	if pub, ok := p.PlatformCerts[serial]; ok && pub != nil {
		return pub, nil
	}
	return nil, fmt.Errorf("wxpay: no public key for serial %q", serial)
}
