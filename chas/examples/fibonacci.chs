// fibonacci.chs
// First ten Fibonacci numbers, two ways. The recursive version is the
// obvious one, the iterative version is the one you'd actually use.

fn fib(n: int) -> int {
    if n < 2 {
        return n
    }
    return fib(n - 1) + fib(n - 2)
}

fn fib_iter(n: int) -> int {
    let a = 0
    let b = 1
    let i = 0
    while i < n {
        let next = a + b
        a = b
        b = next
        i = i + 1
    }
    return a
}

print("recursive:")
for i in 0..10 {
    print(fib(i))
}

print("iterative:")
for i in 0..10 {
    print(fib_iter(i))
}
