int f(int i, int j)
{
  int r;
  r = 0;

  if (i == 0) {
    r = j;
  } else {
    if (i == 1) {
      r = j + 1;
    } else {
      r = f(i - 1, j + 1);
    }
  }

  return r;
}

int main(void) {
    f(2,5);
}