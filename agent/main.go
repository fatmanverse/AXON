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
	"flag"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"os/signal"
	"sync"
	"syscall"
	"time"

	pb "github.com/yimai/axon-agent/gen/agentpb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const agentVersion = "1.0.0"

func main() {
	server := flag.String("server", "127.0.0.1:50051", "控制面 gRPC 地址")
	agentID := flag.String("agent-id", "", "本 Agent 唯一标识(必填)")
	heartbeat := flag.Duration("heartbeat", 10*time.Second, "心跳间隔")
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
		id:        *agentID,
		heartbeat: *heartbeat,
		executed:  make(map[string]bool),
	}
	ag.runForever(ctx, *server)
}

// agent 承载单个 Agent 的连接循环与命令执行状态。
type agent struct {
	id        string
	heartbeat time.Duration

	mu       sync.Mutex
	maxFence int64           // 已见最大 fence,拒绝更旧的命令(§5.4⑥)
	executed map[string]bool // task_id → 已执行(幂等去重,§5.4②)
}

// runForever 建流、跑一轮会话;断开后指数退避 + jitter 重连(§5.4⑦),直到 ctx 取消。
func (a *agent) runForever(ctx context.Context, server string) {
	backoff := time.Second
	const maxBackoff = 30 * time.Second
	for {
		if ctx.Err() != nil {
			return
		}
		err := a.session(ctx, server)
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
func (a *agent) session(ctx context.Context, server string) error {
	conn, err := grpc.NewClient(server, grpc.WithTransportCredentials(insecure.NewCredentials()))
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
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			return false, fmt.Sprintf("写配置失败: %v", err)
		}
		return true, "配置已写入 " + path
	default:
		return false, "不支持的动作: " + cmd.Action
	}
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
