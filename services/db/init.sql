CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price NUMERIC(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    product_id INT REFERENCES products(id),
    amount NUMERIC(10,2) NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT NOW()
);

INSERT INTO products (name, price, stock) VALUES
    ('Laptop Pro 15"',   1299.99, 50),
    ('Wireless Headset', 199.99,  200),
    ('Mechanical Keyboard', 149.99, 300),
    ('USB-C Hub',        79.99,  500),
    ('4K Webcam',        129.99, 150);
