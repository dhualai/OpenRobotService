BINARY := wxpay-service
CMD    := ./cmd/server

.PHONY: all build run test vet fmt tidy docker clean

all: fmt vet test build

build:
	go build -trimpath -o bin/$(BINARY) $(CMD)

run:
	go run $(CMD)

test:
	go test ./... -cover

# -race 需要 cgo；Linux/macOS 可用，Windows 需先安装 gcc 并设 CGO_ENABLED=1
test-race:
	CGO_ENABLED=1 go test ./... -race -cover

vet:
	go vet ./...

fmt:
	gofmt -l -w .

tidy:
	go mod tidy

docker:
	docker build -t $(BINARY):latest .

clean:
	rm -rf bin
