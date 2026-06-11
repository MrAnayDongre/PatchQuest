use std::collections::HashMap;
use std::io::Result;

pub mod utils;

pub struct Config {
    name: String,
    values: HashMap<String, String>,
}

pub enum Status {
    Active,
    Inactive,
    Pending,
}

pub trait Processor {
    fn process(&self, input: &str) -> String;
}

impl Config {
    pub fn new(name: &str) -> Self {
        Config {
            name: name.to_string(),
            values: HashMap::new(),
        }
    }
}

pub fn create_config(name: &str) -> Config {
    Config::new(name)
}

pub async fn fetch_data(url: &str) -> Result<String> {
    Ok(url.to_string())
}
