import { expect, test } from "vitest";
import { unwrap } from "./bridge";

test("业务错误不能显示成成功结果", () => {
  expect(() =>
    unwrap({
      id: "a",
      ok: false,
      error: { code: "invalid_request", message: "资料已改变" },
    }),
  ).toThrow("资料已改变");
});
test("拒绝缺少结果的成功响应", () => {
  expect(() => unwrap({ id: "a", ok: true })).toThrow();
});
test("保留真实结果与空台账", () => {
  expect(unwrap({ id: "a", ok: true, result: { runs: [] } })).toEqual({
    runs: [],
  });
});
