//! 按线程累计差分统计实际用量，拒绝旧回合与重复通知。
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

#[derive(Clone, Default, Deserialize, Serialize, PartialEq, Debug)]
pub struct Tokens {
    pub input: u64,
    pub cached_input: u64,
    pub output: u64,
    pub total: u64,
}
impl Tokens {
    fn read(value: &Value) -> Option<Self> {
        Some(Self { input: value["inputTokens"].as_u64()?,
            cached_input: value["cachedInputTokens"].as_u64().unwrap_or(0),
            output: value["outputTokens"].as_u64()?, total: value["totalTokens"].as_u64()? })
    }
    fn difference(&self, previous: &Self) -> Self {
        Self { input: self.input.saturating_sub(previous.input),
            cached_input: self.cached_input.saturating_sub(previous.cached_input),
            output: self.output.saturating_sub(previous.output), total: self.total.saturating_sub(previous.total) }
    }
    fn add(&mut self, value: &Self) {
        self.input = self.input.saturating_add(value.input);
        self.cached_input = self.cached_input.saturating_add(value.cached_input);
        self.output = self.output.saturating_add(value.output);
        self.total = self.total.saturating_add(value.total);
    }
}

#[derive(Clone, Default, Deserialize, Serialize, Debug)]
pub struct RunUsage {
    pub turns: u64,
    pub images: u64,
    pub tokens: Tokens,
}
struct ThreadUsage {
    run: String,
    turn: String,
    latest: Option<Tokens>,
    resumed: bool,
}
#[derive(Default)]
pub struct UsageLedger {
    pub runs: HashMap<String, RunUsage>,
    threads: HashMap<String, ThreadUsage>,
}
impl UsageLedger {
    pub fn register(&mut self, run: &str, thread: &str, turn: &str, resumed: bool) {
        let previous = self.threads.remove(thread);
        if previous.as_ref().is_some_and(|item| item.run == run && item.turn == turn) {
            self.threads.insert(thread.into(), previous.unwrap());
            return;
        }
        self.runs.entry(run.into()).or_default().turns += 1;
        self.threads.insert(thread.into(), ThreadUsage { run: run.into(), turn: turn.into(),
            latest: previous.and_then(|item| item.latest), resumed });
    }
    pub fn record(&mut self, params: &Value) -> bool {
        let Some(thread) = params["threadId"].as_str() else { return false; };
        let Some(item) = self.threads.get_mut(thread) else { return false; };
        if params["turnId"].as_str() != Some(item.turn.as_str()) { return false; }
        let Some(total) = Tokens::read(&params["tokenUsage"]["total"]) else { return false; };
        if item.latest.as_ref().is_some_and(|old| old.total > total.total) { return false; }
        // 恢复历史线程时只计算本回合新产生的用量；cached/reasoning不另加到total。
        let increment = if let Some(previous) = &item.latest { total.difference(previous) }
            else if item.resumed { let Some(last) = Tokens::read(&params["tokenUsage"]["last"]) else { return false; }; last }
            else { total.clone() };
        self.runs.entry(item.run.clone()).or_default().tokens.add(&increment);
        item.latest = Some(total);
        true
    }
    pub fn images(&mut self, run: &str, count: u64) {
        let item = self.runs.entry(run.into()).or_default();
        item.images = item.images.saturating_add(count);
    }
    pub fn snapshot(&self, run: &str) -> RunUsage { self.runs.get(run).cloned().unwrap_or_default() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn event(thread: &str, turn: &str, total: u64, last: u64) -> Value {
        json!({"threadId":thread,"turnId":turn,"tokenUsage":{
            "total":{"inputTokens":total-10,"cachedInputTokens":20,"outputTokens":10,"totalTokens":total},
            "last":{"inputTokens":last-10,"cachedInputTokens":0,"outputTokens":10,"totalTokens":last}}})
    }
    #[test]
    fn cumulative_and_duplicate_updates_are_not_summed_twice() {
        let mut book=UsageLedger::default();
        book.register("r","t","a",false);
        book.record(&event("t","a",100,100));
        book.record(&event("t","a",100,100));
        book.record(&event("t","a",150,50));
        assert_eq!(book.snapshot("r").tokens.total,150);
        assert_eq!(book.snapshot("r").tokens.cached_input,20);
    }
    #[test]
    fn resumed_thread_excludes_prior_history_and_rejects_old_turns() {
        let mut book=UsageLedger::default();
        book.register("r","t","new",true);
        assert!(!book.record(&event("t","old",1000,100)));
        book.record(&event("t","new",1000,100));
        book.record(&event("t","new",1050,50));
        assert_eq!(book.snapshot("r").tokens.total,150);
        assert!(!book.record(&event("t","new",900,50)));
        assert_eq!(book.snapshot("r").tokens.total,150);
    }
    #[test]
    fn separate_threads_and_turn_registration_have_exact_scope() {
        let mut book=UsageLedger::default();
        book.register("r","a","turn1",false);
        book.register("r","a","turn1",false);
        book.register("r","b","turn2",false);
        book.record(&event("a","turn1",100,100));
        book.record(&event("b","turn2",200,200));
        book.register("other","a","turn3",true);
        book.record(&event("a","turn3",140,40));
        assert_eq!(book.snapshot("r").tokens.total,300);
        assert_eq!(book.snapshot("r").turns,2);
        assert_eq!(book.snapshot("other").tokens.total,40);
    }
}
