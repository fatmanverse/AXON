/**
 * JWT 令牌的读写与订阅。
 *
 * 单独成模块以打破循环依赖:axios 客户端拦截器与 auth store 都依赖它,
 * 但它自身不依赖二者。持久化到 localStorage,刷新页面保持登录态。
 */

const STORAGE_KEY = "yimai.axon.token";

type Listener = (token: string | null) => void;

const listeners = new Set<Listener>();

export function getToken(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(STORAGE_KEY, token);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
  for (const listener of listeners) {
    listener(token);
  }
}

export function onTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
