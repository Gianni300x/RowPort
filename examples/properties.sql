CREATE TABLE properties (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    address text NOT NULL,
    price numeric(12, 2) NOT NULL CHECK (price > 0),
    rooms integer NOT NULL CHECK (rooms > 0)
);
