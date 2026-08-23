// vitest 全局 setup：jsdom 组件测试的 localStorage 桩。
// 背景：Node 22.4+ 原生暴露 globalThis.localStorage（Node 25 下是空壳 object，无 Storage 方法），
// vitest 2.1.9 的 jsdom 环境 populateGlobal 因 `localStorage in global` 为真且不在
// 白名单 KEYS 而跳过 jsdom 的 Storage 覆盖，导致 jsdom 测试里 localStorage 不可用。
// 这里注入一个内存桩，行为对齐标准 Storage（getItem/setItem/removeItem/clear/key/length）。
// 仅影响测试运行，不触碰任何生产代码。

const store = new Map<string, string>();

const stub: Storage = {
  get length() {
    return store.size;
  },
  clear: () => {
    store.clear();
  },
  getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
  key: (index: number) => [...store.keys()][index] ?? null,
  removeItem: (key: string) => {
    store.delete(key);
  },
  setItem: (key: string, value: string) => {
    store.set(key, String(value));
  },
};

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: stub,
});
