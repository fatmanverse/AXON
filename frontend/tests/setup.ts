/**
 * vitest 全局设置:注入 jest-dom 断言,每个用例后清理 DOM 与 localStorage,
 * 保证用例间无残留状态(令牌持久化在 localStorage,不清会串味)。
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// jsdom 未实现 matchMedia,而 AntD 响应式组件(Table/Grid 等)会调用它。
// 提供一个只读的桩:恒不匹配任何 media query,满足组件挂载即可。
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as unknown as MediaQueryList;
}

// jsdom 的 getComputedStyle 不支持第二个 pseudoElt 参数(会抛 Not implemented);
// AntD 的 Table/Drawer 借它测滚动条宽度。包一层丢弃伪元素参数,回退到单参调用。
const _getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (elt: Element): CSSStyleDeclaration => _getComputedStyle(elt);

afterEach(() => {
  cleanup();
  localStorage.clear();
});
