// 一脉 Axon Agent 本体(T4.2,设计 §5.2/§5.3/§5.4)。
//
// 单二进制,主动外连控制面建 gRPC 双向流:
//   - 上行:定时心跳(保活 + 上报 agent 版本)、命令执行结果 ACK(两段 ACK 的第二段)。
//   - 下行:接收 ServerCommand,按 action 执行,回 received/result 两段 ACK。
//
// 断连一致性(§5.4):
//   - fence token 单调递增校验:拒绝低于已见最大 fence 的旧命令(§5.4⑥ 脑裂防护)。
//   - task_id 幂等去重:同一 task 只执行一次,重连补报按 task_id 关联(§5.4②)。
//   - 重连指数退避 + jitter,避免控制面重启时的重连风暴(§5.4⑦)。
package main

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"hash"
	"io"
	"math/rand"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	pb "github.com/yimai/axon-agent/gen/agentpb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
)

const agentVersion = "1.0.0"

func main() {
	server := flag.String("server", "127.0.0.1:50051", "控制面 gRPC 地址")
	agentID := flag.String("agent-id", "", "本 Agent 唯一标识(必填)")
	heartbeat := flag.Duration("heartbeat", 10*time.Second, "心跳间隔")
	insecureMode := flag.Bool("insecure", false, "显式允许明文 gRPC(仅开发/测试)")
	tlsCA := flag.String("tls-ca", "", "控制面客户端 CA 文件")
	tlsCert := flag.String("tls-cert", "", "Agent 客户端证书文件")
	tlsKey := flag.String("tls-key", "", "Agent 客户端私钥文件")
	tlsServerName := flag.String("tls-server-name", "", "控制面证书 ServerName(可选)")
	artifactStagingDir := flag.String("artifact-staging-dir", "/tmp/axon-artifacts", "制品接收目录")
	artifactMaxBytes := flag.Int64("artifact-max-bytes", 1<<30, "单个制品最大字节数")
	artifactChunkMaxBytes := flag.Int("artifact-chunk-max-bytes", 192*1024, "单个制品分块最大字节数")
	configRoots := flag.String("config-roots", "/etc/axon", "允许原子写配置的根目录(逗号分隔)")
	flag.Parse()

	if *agentID == "" {
		fmt.Fprintln(os.Stderr, "错误:--agent-id 必填")
		os.Exit(2)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// 捕获 SIGINT/SIGTERM 优雅退出
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sig
		fmt.Fprintln(os.Stderr, "收到退出信号,关闭 Agent...")
		cancel()
	}()

	ag := &agent{
		id:                    *agentID,
		heartbeat:             *heartbeat,
		executed:              make(map[string]bool),
		artifactStagingDir:    filepath.Clean(*artifactStagingDir),
		artifactMaxBytes:      *artifactMaxBytes,
		artifactChunkMaxBytes: *artifactChunkMaxBytes,
		configRoots:           splitRoots(*configRoots),
	}
	if err := ag.validateLimits(); err != nil {
		fmt.Fprintf(os.Stderr, "Agent 限制配置错误: %v\n", err)
		os.Exit(2)
	}
	creds, err := clientTransportCredentials(*insecureMode, *tlsCA, *tlsCert, *tlsKey, *tlsServerName)
	if err != nil {
		fmt.Fprintf(os.Stderr, "TLS 配置错误: %v\n", err)
		os.Exit(2)
	}
	defer ag.abortAllTransfers()
	ag.runForever(ctx, *server, creds)
}

func clientTransportCredentials(insecureMode bool, caFile, certFile, keyFile, serverName string) (credentials.TransportCredentials, error) {
	if insecureMode {
		return insecure.NewCredentials(), nil
	}
	if strings.TrimSpace(caFile) == "" || strings.TrimSpace(certFile) == "" || strings.TrimSpace(keyFile) == "" {
		return nil, fmt.Errorf("--tls-ca, --tls-cert and --tls-key are required unless --insecure is set")
	}
	caPEM, err := os.ReadFile(caFile)
	if err != nil {
		return nil, fmt.Errorf("read CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, fmt.Errorf("CA file contains no valid certificate")
	}
	cert, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return nil, fmt.Errorf("read client certificate: %w", err)
	}
	return credentials.NewTLS(&tls.Config{
		MinVersion:   tls.VersionTLS13,
		RootCAs:      pool,
		Certificates: []tls.Certificate{cert},
		ServerName:   serverName,
	}), nil
}

// agent 承载单个 Agent 的连接循环与命令执行状态。
type agent struct {
	id        string
	heartbeat time.Duration

	mu       sync.Mutex
	maxFence int64           // 已见最大 fence,拒绝更旧的命令(§5.4⑥)
	executed map[string]bool // task_id → 已执行(幂等去重,§5.4②)

	artifactStagingDir    string
	artifactMaxBytes      int64
	artifactChunkMaxBytes int
	configRoots           []string
	transfers             map[string]*artifactTransfer
}

type artifactTransfer struct {
	file         *os.File
	tempPath     string
	finalPath    string
	expectedSize int64
	expectedSHA  string
	hash         hash.Hash
	written      int64
}

// runForever 建流、跑一轮会话;断开后指数退避 + jitter 重连(§5.4⑦),直到 ctx 取消。
func (a *agent) runForever(ctx context.Context, server string, creds credentials.TransportCredentials) {
	backoff := time.Second
	const maxBackoff = 30 * time.Second
	for {
		if ctx.Err() != nil {
			return
		}
		err := a.session(ctx, server, creds)
		if ctx.Err() != nil {
			return
		}
		if err != nil {
			fmt.Fprintf(os.Stderr, "会话断开: %v,%v 后重连\n", err, backoff)
		}
		// 指数退避 + jitter(±50%),避免重连风暴
		jitter := time.Duration(rand.Int63n(int64(backoff) + 1))
		sleep := backoff/2 + jitter
		select {
		case <-ctx.Done():
			return
		case <-time.After(sleep):
		}
		if backoff < maxBackoff {
			backoff *= 2
			if backoff > maxBackoff {
				backoff = maxBackoff
			}
		}
	}
}

// session 建立一次双向流:起心跳协程 + 接命令循环。任一出错即返回,由 runForever 重连。
func (a *agent) session(ctx context.Context, server string, creds credentials.TransportCredentials) error {
	conn, err := grpc.NewClient(server, grpc.WithTransportCredentials(creds))
	if err != nil {
		return fmt.Errorf("建连失败: %w", err)
	}
	defer conn.Close()

	client := pb.NewAgentServiceClient(conn)
	streamCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	stream, err := client.Connect(streamCtx)
	if err != nil {
		return fmt.Errorf("建流失败: %w", err)
	}

	// 首条心跳建流(带 agent_id 供控制面登记)
	if err := a.sendHeartbeat(stream); err != nil {
		return err
	}

	// 心跳协程:定时保活
	var sendMu sync.Mutex
	go func() {
		ticker := time.NewTicker(a.heartbeat)
		defer ticker.Stop()
		for {
			select {
			case <-streamCtx.Done():
				return
			case <-ticker.C:
				sendMu.Lock()
				err := a.sendHeartbeat(stream)
				sendMu.Unlock()
				if err != nil {
					cancel()
					return
				}
			}
		}
	}()

	// 接命令循环:收 ServerCommand → 执行 → 回 ACK
	for {
		command, err := stream.Recv()
		if err != nil {
			return fmt.Errorf("接收命令失败: %w", err)
		}
		a.handleCommand(stream, &sendMu, command)
	}
}

func (a *agent) sendHeartbeat(stream pb.AgentService_ConnectClient) error {
	return stream.Send(&pb.AgentMessage{
		AgentId: a.id,
		Payload: &pb.AgentMessage_Heartbeat{
			Heartbeat: &pb.Heartbeat{AgentVersion: agentVersion},
		},
	})
}

// handleCommand 执行一条命令并回两段 ACK。fence 过旧或 task 重复则拒绝/跳过(§5.4)。
func (a *agent) handleCommand(
	stream pb.AgentService_ConnectClient, sendMu *sync.Mutex, cmd *pb.ServerCommand,
) {
	// fence 校验:拒绝低于已见最大 fence 的旧命令(§5.4⑥ 脑裂防护)
	a.mu.Lock()
	if cmd.Fence < a.maxFence {
		a.mu.Unlock()
		a.sendResult(stream, sendMu, cmd.TaskId, false,
			fmt.Sprintf("拒绝过期命令: fence %d < %d", cmd.Fence, a.maxFence))
		return
	}
	if cmd.Fence > a.maxFence {
		a.maxFence = cmd.Fence
	}
	// task 幂等去重:已执行过则只回结果,不重复执行(§5.4②)
	if a.executed[cmd.TaskId] {
		a.mu.Unlock()
		a.sendResult(stream, sendMu, cmd.TaskId, true, "已执行(幂等跳过)")
		return
	}
	a.executed[cmd.TaskId] = true
	a.mu.Unlock()

	// 第一段 ACK:已收到(§5.4①)
	a.sendAck(stream, sendMu, cmd.TaskId, pb.AckKind_ACK_KIND_RECEIVED, false, "")

	// 执行命令
	ok, detail := a.execute(cmd)
	// 第二段 ACK:执行结果(推进控制面 task 终态)
	a.sendResult(stream, sendMu, cmd.TaskId, ok, detail)
}

// execute 按 action 执行命令。MVP 支持 exec(shell)与 update_config(写文件);
// 其余 action 返回未支持。生命周期动作(start/stop/restart)复用 exec 由控制面下发命令串。
func (a *agent) execute(cmd *pb.ServerCommand) (bool, string) {
	switch cmd.Action {
	case "exec":
		shellCmd := cmd.Params["command"]
		if shellCmd == "" {
			return false, "exec 缺少 command 参数"
		}
		out, err := exec.Command("sh", "-c", shellCmd).CombinedOutput()
		if err != nil {
			return false, fmt.Sprintf("%s: %v", string(out), err)
		}
		return true, string(out)
	case "update_config":
		path := cmd.Params["path"]
		content := cmd.Params["content"]
		if path == "" {
			return false, "update_config 缺少 path 参数"
		}
		if err := a.atomicWriteConfig(path, []byte(content)); err != nil {
			return false, fmt.Sprintf("写配置失败: %v", err)
		}
		return true, "配置已写入 " + path
	case "artifact_begin":
		if err := a.beginArtifact(cmd.Params); err != nil {
			return false, err.Error()
		}
		return true, "制品传输已开始"
	case "artifact_chunk":
		if err := a.writeArtifactChunk(cmd.Params); err != nil {
			return false, err.Error()
		}
		return true, "制品分块已写入"
	case "artifact_commit":
		if err := a.commitArtifact(cmd.Params["transfer_id"]); err != nil {
			return false, err.Error()
		}
		return true, "制品传输已提交"
	case "artifact_abort":
		a.abortArtifact(cmd.Params["transfer_id"])
		return true, "制品传输已取消"
	default:
		return false, "不支持的动作: " + cmd.Action
	}
}

func splitRoots(raw string) []string {
	roots := make([]string, 0)
	for _, value := range strings.Split(raw, ",") {
		value = strings.TrimSpace(value)
		if value != "" {
			roots = append(roots, filepath.Clean(value))
		}
	}
	return roots
}

func (a *agent) validateLimits() error {
	if !filepath.IsAbs(a.artifactStagingDir) {
		return fmt.Errorf("artifact-staging-dir 必须是绝对路径")
	}
	if a.artifactMaxBytes <= 0 || a.artifactChunkMaxBytes <= 0 {
		return fmt.Errorf("artifact 大小限制必须为正数")
	}
	if len(a.configRoots) == 0 {
		return fmt.Errorf("config-roots 至少包含一个目录")
	}
	for _, root := range a.configRoots {
		if !filepath.IsAbs(root) {
			return fmt.Errorf("config-roots 必须都是绝对路径: %s", root)
		}
	}
	return nil
}

func pathWithinRoot(path string, root string) (string, error) {
	cleanPath, err := filepath.Abs(filepath.Clean(path))
	if err != nil {
		return "", err
	}
	cleanRoot, err := filepath.Abs(filepath.Clean(root))
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(cleanRoot, cleanPath)
	if err != nil || rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		return "", fmt.Errorf("路径不在允许目录内: %s", path)
	}
	return cleanPath, nil
}

func preparePathWithinRoot(path string, root string, dirMode os.FileMode) (string, error) {
	candidate, err := pathWithinRoot(path, root)
	if err != nil {
		return "", err
	}
	cleanRoot, err := filepath.Abs(filepath.Clean(root))
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(cleanRoot, dirMode); err != nil {
		return "", err
	}
	parent := filepath.Dir(candidate)
	realRoot, err := filepath.EvalSymlinks(cleanRoot)
	if err != nil {
		return "", err
	}
	realParent, err := filepath.EvalSymlinks(parent)
	if err != nil {
		return "", err
	}
	return pathWithinRoot(filepath.Join(realParent, filepath.Base(candidate)), realRoot)
}

func (a *agent) artifactPath(path string) (string, error) {
	return preparePathWithinRoot(path, a.artifactStagingDir, 0o700)
}

func validTransferID(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, char := range value {
		if (char < 'a' || char > 'z') && (char < 'A' || char > 'Z') &&
			(char < '0' || char > '9') && char != '-' && char != '_' {
			return false
		}
	}
	return true
}

func (a *agent) beginArtifact(params map[string]string) error {
	transferID := params["transfer_id"]
	if !validTransferID(transferID) {
		return fmt.Errorf("无效 transfer_id")
	}
	finalPath, err := a.artifactPath(params["remote_path"])
	if err != nil {
		return err
	}
	expectedSize, err := strconv.ParseInt(params["size"], 10, 64)
	if err != nil || expectedSize < 0 || expectedSize > a.artifactMaxBytes {
		return fmt.Errorf("无效制品大小或超过上限")
	}
	expectedSHA := strings.ToLower(params["sha256"])
	decodedSHA, err := hex.DecodeString(expectedSHA)
	if err != nil || len(decodedSHA) != sha256.Size {
		return fmt.Errorf("无效制品 SHA-256")
	}
	if a.transfers == nil {
		a.transfers = make(map[string]*artifactTransfer)
	}
	a.abortArtifact(transferID)
	tempPath := finalPath + "." + transferID + ".part"
	file, err := os.OpenFile(tempPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("创建制品临时文件失败: %w", err)
	}
	a.transfers[transferID] = &artifactTransfer{
		file:         file,
		tempPath:     tempPath,
		finalPath:    finalPath,
		expectedSize: expectedSize,
		expectedSHA:  expectedSHA,
		hash:         sha256.New(),
	}
	return nil
}

func (a *agent) writeArtifactChunk(params map[string]string) error {
	transfer := a.transfers[params["transfer_id"]]
	if transfer == nil {
		return fmt.Errorf("制品传输不存在")
	}
	offset, err := strconv.ParseInt(params["offset"], 10, 64)
	if err != nil || offset != transfer.written {
		return fmt.Errorf("制品分块 offset 不连续")
	}
	data, err := base64.StdEncoding.DecodeString(params["data"])
	if err != nil {
		return fmt.Errorf("制品分块 base64 无效")
	}
	if len(data) > a.artifactChunkMaxBytes {
		return fmt.Errorf("制品分块超过上限")
	}
	chunkSHA := fmt.Sprintf("%x", sha256.Sum256(data))
	if !strings.EqualFold(chunkSHA, params["chunk_sha256"]) {
		return fmt.Errorf("制品分块 SHA-256 不匹配")
	}
	if transfer.written+int64(len(data)) > transfer.expectedSize {
		return fmt.Errorf("制品分块超过声明大小")
	}
	written, err := transfer.file.Write(data)
	if err != nil {
		return fmt.Errorf("写制品分块失败: %w", err)
	}
	if written != len(data) {
		return io.ErrShortWrite
	}
	_, _ = transfer.hash.Write(data)
	transfer.written += int64(written)
	return nil
}

func (a *agent) commitArtifact(transferID string) error {
	transfer := a.transfers[transferID]
	if transfer == nil {
		return fmt.Errorf("制品传输不存在")
	}
	if transfer.written != transfer.expectedSize {
		return fmt.Errorf("制品长度不匹配: %d != %d", transfer.written, transfer.expectedSize)
	}
	actualSHA := fmt.Sprintf("%x", transfer.hash.Sum(nil))
	if !strings.EqualFold(actualSHA, transfer.expectedSHA) {
		return fmt.Errorf("制品 SHA-256 不匹配")
	}
	if err := transfer.file.Sync(); err != nil {
		return fmt.Errorf("同步制品失败: %w", err)
	}
	if err := transfer.file.Close(); err != nil {
		return fmt.Errorf("关闭制品失败: %w", err)
	}
	if err := os.Rename(transfer.tempPath, transfer.finalPath); err != nil {
		return fmt.Errorf("提交制品失败: %w", err)
	}
	delete(a.transfers, transferID)
	return nil
}

func (a *agent) abortArtifact(transferID string) {
	if a.transfers == nil {
		return
	}
	transfer := a.transfers[transferID]
	if transfer == nil {
		return
	}
	_ = transfer.file.Close()
	_ = os.Remove(transfer.tempPath)
	delete(a.transfers, transferID)
}

func (a *agent) abortAllTransfers() {
	for transferID := range a.transfers {
		a.abortArtifact(transferID)
	}
}

func (a *agent) atomicWriteConfig(path string, content []byte) error {
	var target string
	var err error
	for _, root := range a.configRoots {
		target, err = preparePathWithinRoot(path, root, 0o750)
		if err == nil {
			break
		}
	}
	if err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(target), ".axon-config-*")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(0o600); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(content); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempPath, target)
}

func (a *agent) sendAck(
	stream pb.AgentService_ConnectClient, sendMu *sync.Mutex,
	taskID string, kind pb.AckKind, ok bool, detail string,
) {
	sendMu.Lock()
	defer sendMu.Unlock()
	_ = stream.Send(&pb.AgentMessage{
		AgentId: a.id,
		Payload: &pb.AgentMessage_Ack{
			Ack: &pb.CommandAck{TaskId: taskID, Kind: kind, Ok: ok, Detail: detail},
		},
	})
}

func (a *agent) sendResult(
	stream pb.AgentService_ConnectClient, sendMu *sync.Mutex,
	taskID string, ok bool, detail string,
) {
	a.sendAck(stream, sendMu, taskID, pb.AckKind_ACK_KIND_RESULT, ok, detail)
}
