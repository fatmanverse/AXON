package main

import (
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"google.golang.org/grpc/credentials/insecure"
)

func TestClientTransportCredentialsRequiresTLSMaterial(t *testing.T) {
	creds, err := clientTransportCredentials(false, "", "", "", "")
	if err == nil {
		t.Fatal("expected missing TLS material to fail")
	}
	if creds != nil {
		t.Fatal("credentials must be nil on validation failure")
	}
}

func TestClientTransportCredentialsAllowsExplicitInsecureMode(t *testing.T) {
	creds, err := clientTransportCredentials(true, "", "", "", "")
	if err != nil {
		t.Fatalf("explicit insecure mode failed: %v", err)
	}
	if _, ok := creds.(insecure.Credentials); !ok {
		t.Fatalf("expected insecure credentials, got %T", creds)
	}
}

func testAgent(t *testing.T) *agent {
	t.Helper()
	return &agent{
		artifactStagingDir:    t.TempDir(),
		artifactMaxBytes:      1024,
		artifactChunkMaxBytes: 4,
		configRoots:           []string{t.TempDir()},
		transfers:             make(map[string]*artifactTransfer),
	}
}

func TestArtifactTransferChecksumsAndCommitsAtomically(t *testing.T) {
	ag := testAgent(t)
	content := []byte("artifact")
	digest := sha256.Sum256(content)
	remote := filepath.Join(ag.artifactStagingDir, "app.tar.gz")
	if err := ag.beginArtifact(map[string]string{
		"transfer_id": "transfer-1",
		"remote_path": remote,
		"size":        "8",
		"sha256":      fmt.Sprintf("%x", digest),
	}); err != nil {
		t.Fatal(err)
	}
	chunkSHA := sha256.Sum256(content[:4])
	if err := ag.writeArtifactChunk(map[string]string{
		"transfer_id":  "transfer-1",
		"offset":       "0",
		"data":         base64.StdEncoding.EncodeToString(content[:4]),
		"chunk_sha256": fmt.Sprintf("%x", chunkSHA),
	}); err != nil {
		t.Fatal(err)
	}
	chunkSHA = sha256.Sum256(content[4:])
	if err := ag.writeArtifactChunk(map[string]string{
		"transfer_id":  "transfer-1",
		"offset":       "4",
		"data":         base64.StdEncoding.EncodeToString(content[4:]),
		"chunk_sha256": fmt.Sprintf("%x", chunkSHA),
	}); err != nil {
		t.Fatal(err)
	}
	if err := ag.commitArtifact("transfer-1"); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(remote)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(content) {
		t.Fatalf("unexpected artifact content: %q", got)
	}
	if _, ok := ag.transfers["transfer-1"]; ok {
		t.Fatal("transfer state must be removed after commit")
	}
}

func TestArtifactTransferRejectsTraversalAndBadChunkChecksum(t *testing.T) {
	ag := testAgent(t)
	if err := ag.beginArtifact(map[string]string{
		"transfer_id": "transfer-2",
		"remote_path": filepath.Join(ag.artifactStagingDir, "..", "escape"),
		"size":        "1",
		"sha256":      fmt.Sprintf("%x", sha256.Sum256([]byte("x"))),
	}); err == nil {
		t.Fatal("expected artifact path traversal to fail")
	}
	content := []byte("x")
	if err := ag.beginArtifact(map[string]string{
		"transfer_id": "transfer-3",
		"remote_path": filepath.Join(ag.artifactStagingDir, "safe"),
		"size":        "1",
		"sha256":      fmt.Sprintf("%x", sha256.Sum256(content)),
	}); err != nil {
		t.Fatal(err)
	}
	if err := ag.writeArtifactChunk(map[string]string{
		"transfer_id":  "transfer-3",
		"offset":       "0",
		"data":         base64.StdEncoding.EncodeToString(content),
		"chunk_sha256": "00",
	}); err == nil {
		t.Fatal("expected bad chunk checksum to fail")
	}
	ag.abortArtifact("transfer-3")
}

func TestAtomicConfigWriteRestrictsRootAndReplacesFile(t *testing.T) {
	ag := testAgent(t)
	path := filepath.Join(ag.configRoots[0], "app", "config.toml")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := ag.atomicWriteConfig(path, []byte("version=2")); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "version=2" {
		t.Fatalf("unexpected config content: %q", got)
	}
	if err := ag.atomicWriteConfig(filepath.Join(ag.configRoots[0], "..", "escape"), []byte("bad")); err == nil {
		t.Fatal("expected config traversal to fail")
	}
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(ag.configRoots[0], "linked")); err != nil {
		t.Fatal(err)
	}
	if err := ag.atomicWriteConfig(filepath.Join(ag.configRoots[0], "linked", "escape"), []byte("bad")); err == nil {
		t.Fatal("expected symlink escape to fail")
	}
}
