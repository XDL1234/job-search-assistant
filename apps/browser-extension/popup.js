const status = document.getElementById("status");
chrome.storage.local.get("status").then((value) => {
  status.textContent = value.status || "等待连接";
});
document.getElementById("connect").addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({
    type: "connect",
    code: document.getElementById("code").value,
  });
  status.textContent = result?.ok
    ? "已连接。可以回到桌面应用配置任务。"
    : result?.error || "连接失败";
});
