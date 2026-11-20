package transfer

import "testing"

func TestStatusTerminal(t *testing.T) {
	cases := map[TransferBillStatus]bool{
		StatusAccepted:        false,
		StatusProcessing:      false,
		StatusWaitUserConfirm: false,
		StatusTransfering:     false,
		StatusCanceling:       false,
		StatusSuccess:         true,
		StatusFail:            true,
		StatusCancelled:       true,
	}
	for s, want := range cases {
		if got := s.IsTerminal(); got != want {
			t.Errorf("IsTerminal(%q) = %v, want %v", s, got, want)
		}
	}
}

// TestCanReusePackageInfo 是「防重复出资」的核心断言：
// 只有 WAIT_USER_CONFIRM 可复用 package_info，其余状态一律不得复用。
func TestCanReusePackageInfo(t *testing.T) {
	if !StatusWaitUserConfirm.CanReusePackageInfo() {
		t.Error("WAIT_USER_CONFIRM must allow reusing package_info")
	}
	for _, s := range []TransferBillStatus{
		StatusAccepted, StatusProcessing, StatusTransfering,
		StatusSuccess, StatusFail, StatusCanceling, StatusCancelled,
	} {
		if s.CanReusePackageInfo() {
			t.Errorf("state %q must NOT allow reusing package_info", s)
		}
	}
}

func TestIsTerminalStringHelper(t *testing.T) {
	if !IsTerminal("SUCCESS") || !IsTerminal(" fail ") {
		t.Error("expected terminal states")
	}
	if IsTerminal("PROCESSING") {
		t.Error("PROCESSING is not terminal")
	}
}

func TestIsAlnum(t *testing.T) {
	valid := []string{"T20261006001", "abc", "123", "AbC123"}
	invalid := []string{"", "T-2026", "T_2026", "T 2026", "T|2026", "中文"}
	for _, v := range valid {
		if !isAlnum(v) {
			t.Errorf("isAlnum(%q) should be true", v)
		}
	}
	for _, v := range invalid {
		if isAlnum(v) {
			t.Errorf("isAlnum(%q) should be false", v)
		}
	}
}
