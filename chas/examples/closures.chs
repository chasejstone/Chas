// closures.chs
// Nested functions capture the variables around them by reference.
// `tick` below reads and writes `n` from `counter`, the same way Python
// `nonlocal` or a JavaScript closure would work.

fn counter() {
    let n = 0
    fn tick() {
        n = n + 1
        print(n)
    }
    tick()
    tick()
    tick()
}

counter()

// Another one: a nested function that only reads a value from the
// enclosing scope.

fn greeter(prefix: string) {
    fn greet(name: string) {
        print(prefix + ", " + name + "!")
    }
    greet("Alice")
    greet("Bob")
}

greeter("Hello")
greeter("Hi")
