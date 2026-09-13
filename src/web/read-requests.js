// One active read per UI region. Duplicate requests join; a new key cancels the
// previous read. The identity check also covers responses already being decoded.
export function createReadLane() {
  let active = null;
  function cancel() {
    active?.controller.abort();
    active = null;
  }
  return {
    cancel,
    run(key, read) {
      if (active?.key === key) return active.promise;
      cancel();
      const task = { key, controller: new AbortController() };
      active = task;
      task.promise = Promise.resolve().then(() => read({
        signal: task.controller.signal,
        current: () => active === task && !task.controller.signal.aborted,
      })).finally(() => {
        if (active === task) active = null;
      });
      return task.promise;
    },
  };
}
