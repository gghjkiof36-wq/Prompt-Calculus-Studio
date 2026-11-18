// The local ComfyUI service issues a new browser credential on each startup.
export function createBridgeRequests(api) {
    let session = {};
    api.addEventListener('reconnected', () => { session = {}; });

    function readConfig() {
        const current = session;
        if (!current.pending) {
            current.pending = request('config', undefined, {cache: 'no-store'})
                .then(config => {
                    if (typeof config?.token !== 'string' || !config.token)
                        throw new Error('無法取得 ComfyUI 擴充的連線憑證。');
                    current.token = config.token;
                    return config;
                }).finally(() => { current.pending = null; });
        }
        return current.pending;
    }

    async function request(path, data, options = {}) {
        const current = session;
        let sentToken;
        if (data !== undefined) {
            if (!current.token) await readConfig();
            if (current !== session) throw new Error('ComfyUI 已重新連線，這次操作尚未送出。');
            sentToken = current.token;
            options = {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Prompt-Studio': sentToken},
                body: JSON.stringify(data),
                ...options
            };
        }
        const response = await api.fetchApi('/prompt_studio/' + path, options);
        // A late response from a previous backend must not clear newer credentials.
        // Only the next request uses a fresh credential. Never replay the rejected POST.
        if (response.status === 403 && sentToken && current === session && sentToken === current.token)
            session = {};
        let result;
        try { result = await response.json(); }
        catch { throw new Error('ComfyUI 擴充未載入或連線中斷，請確認已重新啟動後端。'); }
        if (!response.ok) {
            const error = new Error(result.error ?? '操作失敗，請重新整理頁面後再試。');
            if (response.status === 400 && typeof result.capture_uncommitted === 'string')
                error.uncommittedOperation = result.capture_uncommitted;
            throw error;
        }
        return result;
    }

    return {request, readConfig, hasCredential: () => Boolean(session.token)};
}
