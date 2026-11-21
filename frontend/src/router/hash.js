export function route() {
  const [key, id] = (location.hash.slice(1) || 'cases').split('/');
  return {key, id:Number(id) || 0};
}
// 保留字符串段：离线批单号是 UUID 之类的幂等键，不能被强转成数字。
export function routeRaw() {
  const [key, id = ''] = (location.hash.slice(1) || 'cases').split('/');
  return {key, id};
}
export function go(to) {
  location.hash = to;
}
