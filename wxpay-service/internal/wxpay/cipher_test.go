package wxpay

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"testing"
)

// TestDecryptResourceRoundTrip 验证 AEAD_AES_256_GCM 解密与微信侧加密流程自洽。
func TestDecryptResourceRoundTrip(t *testing.T) {
	apiV3Key := "0123456789abcdef0123456789abcdef" // 32 字节
	nonce := "abcdefghijkl"
	associatedData := "transaction"
	plaintext := `{"out_bill_no":"T20261006001","state":"SUCCESS"}`

	block, err := aes.NewCipher([]byte(apiV3Key))
	if err != nil {
		t.Fatalf("new cipher: %v", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		t.Fatalf("new gcm: %v", err)
	}
	ciphertext := gcm.Seal(nil, []byte(nonce), []byte(plaintext), []byte(associatedData))

	got, err := DecryptResource(apiV3Key, nonce, associatedData, base64.StdEncoding.EncodeToString(ciphertext))
	if err != nil {
		t.Fatalf("DecryptResource: %v", err)
	}
	if string(got) != plaintext {
		t.Fatalf("unexpected plaintext: got %q want %q", got, plaintext)
	}
}

func TestDecryptResourceRejectsBadKey(t *testing.T) {
	if _, err := DecryptResource("short-key", "nonce", "", base64.StdEncoding.EncodeToString([]byte("x"))); err == nil {
		t.Fatal("expected error for non-32-byte key")
	}
}

func TestDecryptResourceDetectsTampering(t *testing.T) {
	apiV3Key := "0123456789abcdef0123456789abcdef"
	nonce := "abcdefghijkl"
	block, _ := aes.NewCipher([]byte(apiV3Key))
	gcm, _ := cipher.NewGCM(block)
	ct := gcm.Seal(nil, []byte(nonce), []byte("hello"), []byte("ad"))

	ct[0] ^= 0xff // 篡改密文
	if _, err := DecryptResource(apiV3Key, nonce, "ad", base64.StdEncoding.EncodeToString(ct)); err == nil {
		t.Fatal("expected authentication failure for tampered ciphertext")
	}
}

func TestEncryptOAEP(t *testing.T) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate key: %v", err)
	}
	out, err := EncryptOAEP(&priv.PublicKey, "张三")
	if err != nil {
		t.Fatalf("EncryptOAEP: %v", err)
	}
	raw, err := base64.StdEncoding.DecodeString(out)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(raw) != priv.Size() {
		t.Fatalf("unexpected ciphertext length: got %d want %d", len(raw), priv.Size())
	}
	if _, err := EncryptOAEP(nil, "张三"); err == nil {
		t.Fatal("expected error when public key is nil")
	}
}
