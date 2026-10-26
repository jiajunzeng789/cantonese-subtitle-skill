#!/bin/bash
set -u
bundle_dir="$(cd "$(dirname "$0")" && pwd)"
if [ "$(uname -m)" != "arm64" ]; then
  if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
    exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
  fi
  echo "此安装包只支持苹果芯片 Mac（M1及以后）。"
  read -r -p "按回车关闭窗口…"
  exit 1
fi
unset PYTHONHOME PYTHONPATH
export PYTHONUNBUFFERED=1
python_bin="$bundle_dir/payload/python/bin/python3"
if [ ! -x "$python_bin" ]; then
  echo "安装包不完整。请先完整解压，勿只复制这个command文件。"
  read -r -p "按回车关闭窗口…"
  exit 1
fi
"$python_bin" "$bundle_dir/install.py" --payload "$bundle_dir/payload" "$@"
result=$?
if [ "$result" -ne 0 ]; then
  echo "安装未完成，已保留日志。修正提示的问题后可再次双击继续。"
fi
read -r -p "按回车关闭窗口…"
exit "$result"
