import axios from "axios";
import { Message } from "element-ui";

const service = axios.create({
  baseURL: "/api",
  // 短于 Nginx proxy_read_timeout(210s)，长于后端单次模型调用上限，
  // 避免浏览器在网关仍正常等待时提前中断。
  timeout: 190000,
});

function readableRequestError(error) {
  const status = Number(error && error.response && error.response.status);
  const responseMessage = String(
    (error && error.response && error.response.data && error.response.data.message) || "",
  ).trim();
  if (responseMessage && !/^request failed(?: with status code \d+)?$/i.test(responseMessage)) {
    return responseMessage;
  }
  if (status === 504 || status === 408 || (error && error.code === "ECONNABORTED")) {
    return "请求处理超时，请稍后重试";
  }
  if (status === 502 || status === 503) {
    return "服务暂时不可用，请稍后重试";
  }
  if (status) {
    return `请求失败（HTTP ${status}）`;
  }
  const rawMessage = String((error && error.message) || "").trim();
  if (/network error|failed to fetch/i.test(rawMessage)) {
    return "网络连接异常，请检查服务状态后重试";
  }
  if (/timeout/i.test(rawMessage)) {
    return "请求处理超时，请稍后重试";
  }
  return rawMessage && !/^request failed(?: with status code \d+)?$/i.test(rawMessage)
    ? rawMessage
    : "网络请求失败，请稍后重试";
}

service.interceptors.response.use(
  (res) => {
    const payload = res.data || {};
    if (payload.code && payload.code !== 200) {
      const error = new Error(payload.message || "Request failed");
      error.response = res;
      error.config = res.config;
      const message = readableRequestError(error);
      error.userMessage = message;
      error.message = message;
      if (!(res.config && res.config.silentError)) {
        Message.error(message);
      }
      return Promise.reject(error);
    }
    return payload;
  },
  (err) => {
    const message = readableRequestError(err);
    if (!(err && err.config && err.config.silentError)) {
      Message.error(message);
    }
    if (err && !err.userMessage) {
      err.userMessage = message;
    }
    if (err) {
      err.message = message;
    }
    return Promise.reject(err);
  }
);

export default service;
