"""Main-thread policy changes signal worker cancellation without worker UI reads."""
import weakref

class NetworkPolicy:
    def __init__(self,window): self.window=window; self.pending=weakref.WeakSet()
    def refresh(self):
        online=self.window.state['settings'].get('online',True) and not self.window.closing
        if not online:
            for cancel in self.pending: cancel.set()
        return online
    def track(self,cancel):
        self.pending.add(cancel)
        if not self.refresh(): cancel.set()

def network_policy(window):
    if not hasattr(window,'civitai_network'): window.civitai_network=NetworkPolicy(window)
    return window.civitai_network
