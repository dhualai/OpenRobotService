package wxpay

import (
	"crypto/rand"
	"crypto/rsa"
	"fmt"
	"strings"
	"testing"
)

func TestBuildAuthorizationAndVerify(t *testing.T) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	cred := Credential{MchID: "1900009191", SerialNo: "1DDE55AD98E", PrivateKey: priv}

	body := []byte(`{"out_bill_no":"T20261006001"}`)
	auth, err := BuildAuthorization(cred, "POST", "/v3/fund-app/mch-transfer/transfer-bills", body)
	if err != nil {
		t.Fatalf("BuildAuthorization: %v", err)
	}
	if !strings.HasPrefix(auth, AuthType+" ") {
		t.Fatalf("unexpected auth prefix: %q", auth)
	}
	for _, want := range []string{`mchid="1900009191"`, `nonce_str="`, `signature="`, `timestamp="`, `serial_no="1DDE55AD98E"`} {
		if !strings.Contains(auth, want) {
			t.Fatalf("auth header missing %q: %s", want, auth)
		}
	}

	// 提取字段后按同样的验签串格式校验。
	fields := parseAuth(t, auth)
	// 验签串与请求签名串仅差 method 与 url 两行，这里直接复用 SignMessage 生成可校验的组合。
	ts := mustAtoi(t, fields["timestamp"])
	sig, err := SignMessage(cred, "POST", "/v3/fund-app/mch-transfer/transfer-bills", ts, fields["nonce_str"], body)
	if err != nil {
		t.Fatalf("SignMessage: %v", err)
	}
	if sig != fields["signature"] {
		t.Fatal("signature is not reproducible for the same inputs")
	}
}

func TestVerifySignatureRejectsTamperedBody(t *testing.T) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	cred := Credential{MchID: "1900009191", SerialNo: "S1", PrivateKey: priv}

	const ts = "1700000000"
	const nonce = "nonce123"
	// 应答/回调使用三行验签串，与请求签名串格式不同。
	sig, err := SignResponseMessage(cred, ts, nonce, `{"a":1}`)
	if err != nil {
		t.Fatalf("SignResponseMessage: %v", err)
	}

	if err := VerifySignature(&priv.PublicKey, ts, nonce, `{"a":1}`, sig); err != nil {
		t.Fatalf("expected valid signature, got %v", err)
	}
	if err := VerifySignature(&priv.PublicKey, ts, nonce, `{"a":2}`, sig); err == nil {
		t.Fatal("expected failure for tampered body")
	}
	if err := VerifySignature(&priv.PublicKey, "", nonce, `{"a":1}`, sig); err == nil {
		t.Fatal("expected failure for missing timestamp")
	}
}

func TestStaticKeyProvider(t *testing.T) {
	priv, _ := rsa.GenerateKey(rand.Reader, 2048)
	other, _ := rsa.GenerateKey(rand.Reader, 2048)
	p := NewStaticKeyProvider("PUB_KEY_ID_1", &priv.PublicKey, map[string]*rsa.PublicKey{
		"CERT_SERIAL_1": &other.PublicKey,
	})

	if got, err := p.PublicKeyBySerial("PUB_KEY_ID_1"); err != nil || got != &priv.PublicKey {
		t.Fatalf("expected wechat pay public key, got %v err=%v", got, err)
	}
	if got, err := p.PublicKeyBySerial("CERT_SERIAL_1"); err != nil || got != &other.PublicKey {
		t.Fatalf("expected platform cert key, got %v err=%v", got, err)
	}
	if _, err := p.PublicKeyBySerial("UNKNOWN"); err == nil {
		t.Fatal("expected error for unknown serial")
	}
}

// parseAuth 解析 Authorization 头的 key="value" 字段。
func parseAuth(t *testing.T, auth string) map[string]string {
	t.Helper()
	out := map[string]string{}
	for _, part := range strings.Split(strings.TrimPrefix(auth, AuthType+" "), ",") {
		kv := strings.SplitN(strings.TrimSpace(part), "=", 2)
		if len(kv) != 2 {
			t.Fatalf("bad auth segment %q", part)
		}
		out[kv[0]] = strings.Trim(kv[1], `"`)
	}
	return out
}

func mustAtoi(t *testing.T, s string) int64 {
	t.Helper()
	var v int64
	if _, err := fmt.Sscanf(s, "%d", &v); err != nil {
		t.Fatalf("parse int %q: %v", s, err)
	}
	return v
}
