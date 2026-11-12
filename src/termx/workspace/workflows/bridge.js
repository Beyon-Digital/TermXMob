/* Trusted QuickJS bootstrap. No Python callable or host object enters this realm. */
(() => {
  "use strict";
  const encode = JSON.stringify.bind(JSON);
  const decode = JSON.parse.bind(JSON);
  const freeze = Object.freeze.bind(Object);
  const keys = Object.keys.bind(Object);
  const isArray = Array.isArray.bind(Array);
  const text = String;
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const pending = new Map();
  let outbox = [];
  let nextId = 0;
  let nextPhase = 0;
  let agentCount = 0;
  let compiled;
  let outcome;
  let limits;

  function limited(code, message) {
    const error = new Error(message);
    error.code = code;
    return error;
  }

  function failure(value) {
    let message = "Workflow failed";
    let code = "script_error";
    try {
      message = text(value && value.message !== undefined ? value.message : value).slice(0, 2000);
      if (value && typeof value.code === "string") code = value.code.slice(0, 80);
    } catch (_) {}
    return { code, message };
  }

  function immutable(value) {
    if (value && typeof value === "object") {
      for (const key of keys(value)) immutable(value[key]);
      freeze(value);
    }
    return value;
  }

  function request(operation, arguments_) {
    ++nextId;
    // Copy through JSON before enqueueing: guest objects never cross the bridge.
    const encoded = encode(arguments_);
    if (encoded === undefined || encoded.length > limits.frame_bytes) {
      throw limited("output_limit", "Workflow host-call arguments exceed the JSON limit");
    }
    const id = text(nextId);
    outbox.push({ type: "call", id, operation, arguments: decode(encoded) });
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  const api = freeze({
    agent(spec) {
      if (!spec || typeof spec !== "object" || isArray(spec)) throw new TypeError("agent() expects an object");
      if (++agentCount > limits.max_agents && limits.max_agents !== null) throw limited("agent_limit", "Workflow agent limit exceeded");
      return request("agent", spec);
    },
    async parallel(items, callback) {
      if (!isArray(items) || typeof callback !== "function") {
        throw new TypeError("parallel() expects an array and a function");
      }
      return await Promise.all(items.map((item, index) => callback(item, index)));
    },
    async pipeline(initial, stages) {
      if (!isArray(stages)) throw new TypeError("pipeline() expects an array of functions");
      let value = initial;
      for (const stage of stages) {
        if (typeof stage !== "function") throw new TypeError("pipeline stages must be functions");
        value = await stage(value);
      }
      return value;
    },
    async phase(name, callback) {
      if (typeof name !== "string" || !name.trim() || name.length > 120 || typeof callback !== "function") {
        throw new TypeError("phase() expects a name and a function");
      }
      const phase_id = "phase-" + (++nextPhase);
      await request("phase", { phase_id, name, state: "start" });
      try {
        const value = await callback();
        await request("phase", { phase_id, name, state: "end", status: "completed" });
        return value;
      } catch (error) {
        await request("phase", { phase_id, name, state: "end", status: "failed", error: failure(error) });
        throw error;
      }
    },
    log(message, data) {
      if (typeof message !== "string" || message.length > 2000) throw new TypeError("log() expects a string of at most 2000 characters");
      return request("log", data === undefined ? { message } : { message, data });
    },
  });

  return freeze({
    compile(source, configuration) {
      limits = decode(configuration);
      // Function construction compiles only. It never evaluates the user's body.
      compiled = new AsyncFunction("__input", "__api", '"use strict"; const args = __input; const { agent, parallel, pipeline, phase, log } = __api;\n' + source);
    },
    start(input) {
      compiled(immutable(decode(input)), api).then(
        value => {
          try {
            const encoded = encode(value === undefined ? null : value);
            if (encoded === undefined) throw new TypeError("Workflow result must be JSON");
            outcome = { type: "result", result: decode(encoded) };
          } catch (error) { outcome = { type: "error", error: failure(error) }; }
        },
        error => { outcome = { type: "error", error: failure(error) }; },
      );
    },
    settle(response) {
      const item = decode(response);
      const waiter = pending.get(item.id);
      if (!waiter) throw new Error("Unknown or duplicate workflow response");
      pending.delete(item.id);
      if (item.ok === true) waiter.resolve(item.value);
      else {
        const detail = item.error || {};
        const error = new Error(typeof detail.message === "string" ? detail.message : "Workflow host call failed");
        error.code = typeof detail.code === "string" ? detail.code : "host_call_failed";
        waiter.reject(error);
      }
    },
    poll() {
      const requests = outbox;
      outbox = [];
      return encode({ requests, pending: pending.size, outcome: pending.size === 0 ? outcome : null });
    },
  });
})()
