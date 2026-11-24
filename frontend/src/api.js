import { toast } from "sonner";

export async function api(path, body, { keepalive = false } = {}) {
  const upload = body instanceof FormData;
  const options = body === undefined ? {} : {
    method: "POST",
    headers: upload ? { "X-LQA-UI": "1" } : { "Content-Type": "application/json", "X-LQA-UI": "1" },
    body: upload ? body : JSON.stringify(body),
    keepalive,
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || "请求失败。"), { status: response.status });
  return data;
}

export function reportError(error) {
  toast.error(error.message || String(error), { duration: 6500 });
}

export function displayTime(shot) {
  return new Date(shot.created * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

export function openShot(id) {
  location.hash = "shot=" + id;
}

export function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取所选文件。"));
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.readAsDataURL(file);
  });
}
