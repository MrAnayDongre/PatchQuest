#include <stdio.h>
#include <stdlib.h>

struct Point {
    int x;
    int y;
};

int add(int a, int b) {
    return a + b;
}

void print_point(struct Point* p) {
    printf("(%d, %d)\n", p->x, p->y);
}

int main(int argc, char* argv[]) {
    struct Point p = {1, 2};
    print_point(&p);
    return 0;
}
