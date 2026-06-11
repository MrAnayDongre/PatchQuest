#include <iostream>
#include <string>

namespace patchquest {

class Engine {
public:
    void start() {}
    void stop() {}
};

struct Config {
    std::string name;
    int value;
};

}

int process(int x) {
    return x * 2;
}

int main() {
    patchquest::Engine engine;
    engine.start();
    return 0;
}
